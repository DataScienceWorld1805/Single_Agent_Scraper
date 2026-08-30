"""Orquestación pública del Single Agent Scraper."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from scraper_agent.config import Settings, get_settings
from scraper_agent.extractor import StructuredExtractor
from scraper_agent.image_downloader import attach_local_images
from scraper_agent.logging_setup import get_logger, setup_logging
from scraper_agent.models import GenericPage, ProductListing, ScrapedImage, ScrapedResult
from scraper_agent.scraper import HybridScraper

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

PRODUCT_GOAL_HINTS = (
    "producto",
    "product",
    "precio",
    "price",
    "mercado libre",
    "mercadolibre",
    "ebay",
    "amazon",
    "listing",
    "tienda",
    "foto",
    "imagen",
)


def _is_product_image(url: str) -> bool:
    lower = url.lower()
    if any(x in lower for x in (".svg", "frontend-assets", "homes-palpatine", "pixel")):
        return False
    return any(token in lower for token in ("d_q_np", "d_nq_np", "/product", "/item", "mlstatic.com"))


def _backfill_product_images(listing: ProductListing, detected: list[str]) -> ProductListing:
    """Si el LLM omitió images[], asigna fotos de producto detectadas en el DOM."""
    pool = [u for u in detected if _is_product_image(u)]
    if not pool:
        return listing
    cursor = 0
    for item in listing.items:
        if item.images:
            continue
        if cursor < len(pool):
            item.images = [ScrapedImage(url=pool[cursor], alt=item.title)]
            cursor += 1
    return listing


def _infer_schema(goal: str | None, response_model: type[BaseModel] | None) -> type[BaseModel]:
    if response_model is not None:
        return response_model
    if goal:
        lowered = goal.lower()
        if any(h in lowered for h in PRODUCT_GOAL_HINTS):
            return ProductListing
    # Default profesional: productos (marketplaces / e-commerce)
    return ProductListing


def _persist_json(result: ScrapedResult[BaseModel], output_dir: str, url: str) -> Path:
    from hashlib import sha1
    from urllib.parse import urlparse

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).netloc.replace(":", "_") or "page"
    digest = sha1(url.encode()).hexdigest()[:8]
    path = out / f"{host}_{digest}.json"
    path.write_text(result.to_json(), encoding="utf-8")
    return path


async def scrape(
    url: str,
    *,
    goal: str | None = None,
    response_model: type[T] | None = None,
    download_images: bool | None = None,
    provider: Literal["groq", "gemini"] | None = None,
    settings: Settings | None = None,
    persist: bool = True,
) -> ScrapedResult[T]:
    """
    Pipeline completo:
      1) Fetch híbrido (httpx → Playwright)
      2) Poda DOM + Markdown
      3) Extracción LLM tipada (Groq/Gemini) con autocorrección
      4) Descarga opcional de fotos de producto
    """
    cfg = settings or get_settings()
    setup_logging(cfg.log_level)

    schema = _infer_schema(goal, response_model)
    effective_goal = goal or (
        "Extraer todos los productos visibles con título, precio, moneda, "
        "descripción e imágenes (URLs)."
        if schema is ProductListing
        else "Extraer el contenido principal de la página de forma estructurada."
    )
    should_download = cfg.download_images if download_images is None else download_images

    log.info("scrape_start", url=url, schema=schema.__name__, provider=provider or cfg.llm_provider)

    async with HybridScraper(cfg) as scraper:
        fetched = await scraper.fetch(url)

    extractor = StructuredExtractor(cfg)
    data = await extractor.extract(
        url=url,
        markdown=fetched.document.markdown,
        image_urls=fetched.document.image_urls,
        response_model=schema,  # type: ignore[arg-type]
        goal=effective_goal,
        provider=provider,
    )

    if isinstance(data, ProductListing):
        data = _backfill_product_images(data, fetched.document.image_urls)

    if should_download and isinstance(data, ProductListing):
        data = await attach_local_images(data, settings=cfg)

    # Si el schema es GenericPage y no hay título, completar desde cleaner
    if isinstance(data, GenericPage) and not data.title and fetched.document.title:
        data.title = fetched.document.title

    result: ScrapedResult[T] = ScrapedResult(
        data=data,  # type: ignore[arg-type]
        fetch_method=fetched.method,
        warnings=list(fetched.warnings),
        raw_markdown_chars=len(fetched.document.markdown),
        blocked=fetched.blocked,
        image_urls_detected=fetched.document.image_urls,
    )

    if persist:
        path = _persist_json(result, cfg.output_dir, url)  # type: ignore[arg-type]
        log.info("scrape_persisted", path=str(path))
        if isinstance(data, ProductListing):
            try:
                from scraper_agent.reporter import generate_reports

                report_paths = await generate_reports(
                    result,  # type: ignore[arg-type]
                    output_dir=cfg.output_dir,
                    json_path=path,
                    make_pdf=True,
                )
                log.info(
                    "report_generated",
                    html=str(report_paths.html_path),
                    pdf=str(report_paths.pdf_path),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("report_generation_failed", error=str(exc))
                result.warnings.append(f"No se pudo generar informe HTML/PDF: {exc}")
                path.write_text(result.to_json(), encoding="utf-8")

    log.info(
        "scrape_done",
        method=fetched.method,
        blocked=fetched.blocked,
        warnings=len(result.warnings),
    )
    return result
