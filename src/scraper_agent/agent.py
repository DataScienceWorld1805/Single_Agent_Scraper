"""Orquestación pública del Single Agent Scraper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

from scraper_agent.config import Settings, get_settings
from scraper_agent.extractor import StructuredExtractor
from scraper_agent.image_downloader import attach_local_images
from scraper_agent.logging_setup import get_logger, setup_logging
from scraper_agent.models import GenericPage, ProductItem, ProductListing, ScrapedImage, ScrapedResult
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
    "coto",
    "oferta",
)


def _is_product_image(url: str) -> bool:
    lower = url.lower()
    if any(x in lower for x in (".svg", "frontend-assets", "homes-palpatine", "pixel")):
        return False
    return any(
        token in lower
        for token in (
            "d_q_np",
            "d_nq_np",
            "/product",
            "/item",
            "mlstatic.com",
            "cotodigital",
            "sitios/fotos",
            "vteximg.com",
            "carrefourar",
            "/arquivos/ids/",
        )
    )


def _backfill_product_images(listing: ProductListing, detected: list[str]) -> ProductListing:
    """Si el LLM omitió images[], asigna fotos de producto detectadas en el DOM."""
    pool = [u for u in detected if _is_product_image(u)]
    if not pool:
        pool = [u for u in detected if u.startswith("http")]
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


def listing_from_structured_products(
    url: str,
    products: list[dict[str, Any]],
    *,
    page_title: str | None = None,
    limit: int = 40,
) -> ProductListing:
    """Convierte productos ya tipados (API) a ProductListing sin pasar por el LLM."""
    items: list[ProductItem] = []
    for prod in products[:limit]:
        title = str(prod.get("title") or "").strip()
        if not title:
            continue
        image = prod.get("image")
        images: list[ScrapedImage] = []
        if isinstance(image, str) and image.startswith("http"):
            images = [ScrapedImage(url=image, alt=title)]
        price = prod.get("price")
        price_val: float | None
        try:
            price_val = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_val = None
        items.append(
            ProductItem(
                title=title,
                price=price_val,
                currency=(str(prod["currency"]) if prod.get("currency") else None),
                description=(str(prod["description"]) if prod.get("description") else title),
                images=images,
                url=(str(prod["url"]) if prod.get("url") else None),
                notes=(str(prod["brand"]) if prod.get("brand") else None),
            )
        )
    host = urlparse(url).netloc
    return ProductListing(
        source_url=url,
        items=items,
        page_title=page_title or f"Productos — {host}",
    )


def _infer_schema(goal: str | None, response_model: type[BaseModel] | None) -> type[BaseModel]:
    if response_model is not None:
        return response_model
    if goal:
        lowered = goal.lower()
        if any(h in lowered for h in PRODUCT_GOAL_HINTS):
            return ProductListing
    return ProductListing


def _persist_json(result: ScrapedResult[BaseModel], output_dir: str, url: str) -> Path:
    from hashlib import sha1

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
      1) Fetch híbrido (API catálogo / httpx → Playwright)
      2) Poda DOM + Markdown (o productos estructurados de API)
      3) Extracción LLM tipada — o bypass si ya hay productos API
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

    warnings = list(fetched.warnings)
    data: BaseModel

    # Si ya tenemos productos estructurados (Coto API / XHR), no dependemos del LLM
    if schema is ProductListing and fetched.structured_products:
        data = listing_from_structured_products(
            url,
            fetched.structured_products,
            page_title=fetched.document.title,
            limit=cfg.max_products,
        )
        warnings.append(
            f"Productos tomados directo de la API/JSON ({len(data.items)} ítems); LLM omitido"
        )
        log.info("structured_products_used", count=len(data.items))
    else:
        extractor = StructuredExtractor(cfg)
        data = await extractor.extract(
            url=url,
            markdown=fetched.document.markdown,
            image_urls=fetched.document.image_urls,
            response_model=schema,  # type: ignore[arg-type]
            goal=effective_goal,
            provider=provider,
        )
        # Fallback: si el LLM devolvió vacío pero hay imágenes/API parciales en markdown no aplica;
        # si structured llegó vacío y LLM vacío, queda vacío.

    if isinstance(data, ProductListing):
        data = _backfill_product_images(data, fetched.document.image_urls)

    if should_download and isinstance(data, ProductListing) and data.items:
        data = await attach_local_images(data, settings=cfg)

    if isinstance(data, GenericPage) and not data.title and fetched.document.title:
        data.title = fetched.document.title

    result: ScrapedResult[T] = ScrapedResult(
        data=data,  # type: ignore[arg-type]
        fetch_method=fetched.method,
        warnings=warnings,
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
        items=len(data.items) if isinstance(data, ProductListing) else None,
    )
    return result
