"""Fase 1: fetch híbrido httpx → Playwright Stealth (+ harvest de APIs JSON)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from playwright.async_api import Browser, async_playwright
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper_agent.anti_bot import (
    BLOCK_STATUS_CODES,
    build_headers,
    looks_like_block,
    looks_like_js_heavy,
    pick_proxy,
    playwright_proxy_config,
)
from scraper_agent.config import Settings, get_settings
from scraper_agent.html_cleaner import CleanDocument, html_to_clean_document
from scraper_agent.logging_setup import get_logger
from scraper_agent.product_json import (
    extract_products_from_jsonld,
    extract_products_from_payload,
    looks_like_product_api,
    products_image_urls,
    products_to_markdown,
)
from scraper_agent.site_adapters import try_fetch_catalog_products

log = get_logger(__name__)


class FetchError(Exception):
    """Error irrecuperable al obtener la página."""

    def __init__(self, message: str, *, blocked: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.blocked = blocked
        self.status_code = status_code


@dataclass
class FetchResult:
    url: str
    html: str
    method: Literal["httpx", "playwright", "api"]
    status_code: int
    document: CleanDocument
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    structured_products: list[dict[str, Any]] = field(default_factory=list)


def _merge_products_into_document(
    doc: CleanDocument,
    products: list[dict[str, Any]],
    *,
    max_chars: int,
) -> CleanDocument:
    if not products:
        return doc
    api_md = products_to_markdown(products)
    images = list(dict.fromkeys([*doc.image_urls, *products_image_urls(products)]))
    # Preferir productos API cuando el markdown DOM es pobre
    useful = len(
        doc.markdown.replace("Detected product images", "")
        .replace("[...truncated for LLM context budget...]", "")
        .strip()
    )
    if useful < 400:
        markdown = api_md
    else:
        markdown = f"{doc.markdown}\n\n{api_md}"
    if len(markdown) > max_chars:
        markdown = markdown[:max_chars] + "\n\n[...truncated for LLM context budget...]"
    return CleanDocument(
        markdown=markdown,
        image_urls=images,
        title=doc.title,
        pruned_html_chars=doc.pruned_html_chars,
    )


async def _fetch_httpx(url: str, settings: Settings) -> tuple[int, str, list[str], str]:
    warnings: list[str] = []
    proxy = pick_proxy(settings)
    headers = build_headers(settings)

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(settings.max_fetch_retries),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=settings.http_timeout,
                proxy=proxy,
                headers=headers,
            ) as client:
                response = await client.get(url)
                final_url = str(response.url)
                log.info(
                    "httpx_fetch",
                    url=url,
                    final_url=final_url,
                    status=response.status_code,
                    attempt=attempt.retry_state.attempt_number,
                    proxy=bool(proxy),
                )
                if response.status_code in BLOCK_STATUS_CODES:
                    warnings.append(f"HTTP {response.status_code} en intento httpx")
                    if attempt.retry_state.attempt_number < settings.max_fetch_retries:
                        proxy = pick_proxy(settings)
                        headers = build_headers(settings)
                        raise httpx.TransportError(f"retryable status {response.status_code}")
                html = response.content.decode(response.encoding or "utf-8", errors="replace")
                if not html.strip():
                    html = response.text
                return response.status_code, html, warnings, final_url

    raise FetchError("httpx agotó reintentos", blocked=True)


async def _fetch_playwright(
    url: str,
    settings: Settings,
) -> tuple[int, str, list[str], list[dict[str, Any]]]:
    warnings: list[str] = []
    proxy = pick_proxy(settings)
    headers = build_headers(settings)
    proxy_cfg = playwright_proxy_config(proxy)
    captured_products: list[dict[str, Any]] = []

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg

        browser: Browser = await p.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                user_agent=headers["User-Agent"],
                locale="es-AR",
                viewport={"width": 1366, "height": 900},
                java_script_enabled=True,
                extra_http_headers={
                    k: v for k, v in headers.items() if k.lower() != "user-agent"
                },
            )
            page = await context.new_page()

            try:
                from playwright_stealth import Stealth

                await Stealth().apply_stealth_async(page)
            except Exception:
                try:
                    from playwright_stealth import stealth_async

                    await stealth_async(page)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"playwright-stealth no aplicado: {exc}")
                    await page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )

            async def on_response(resp) -> None:  # noqa: ANN001
                try:
                    req_url = resp.url
                    if resp.status != 200 or not looks_like_product_api(req_url):
                        return
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if "json" not in ctype and "javascript" not in ctype:
                        return
                    data = await resp.json()
                    products = extract_products_from_payload(
                        data, base_url=url, limit=settings.max_products
                    )
                    if products:
                        captured_products.extend(products)
                except Exception:  # noqa: BLE001
                    return

            page.on("response", on_response)

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.playwright_timeout,
            )
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=min(20_000, settings.playwright_timeout),
                )
            except Exception:  # noqa: BLE001
                warnings.append("networkidle timeout; se usa DOM actual")

            # Esperar señales de listado/producto (incl. supers / Angular)
            for selector in (
                "img[src*='D_Q_NP']",
                "img[src*='D_NQ_NP']",
                ".ui-search-layout",
                ".poly-card",
                "[data-testid='item']",
                "app-product-card",
                "card-product",
                ".product-card",
                ".card-product",
                "img[src*='cotodigital']",
                "img[src*='sitios/fotos']",
                "main img",
            ):
                try:
                    await page.wait_for_selector(selector, timeout=8_000)
                    break
                except Exception:  # noqa: BLE001
                    continue

            # Extra wait for SPA hydration + lazy load
            try:
                await page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001
                pass

            try:
                await page.evaluate(
                    """async () => {
                        await new Promise(resolve => {
                            let total = 0;
                            const step = () => {
                                window.scrollBy(0, 800);
                                total += 800;
                                if (total >= document.body.scrollHeight || total > 8000) {
                                    resolve();
                                } else {
                                    setTimeout(step, 200);
                                }
                            };
                            step();
                        });
                    }"""
                )
                await page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001
                pass

            html = await page.content()
            status = response.status if response else 200
            # dedupe captured
            deduped: list[dict[str, Any]] = []
            seen: set[str] = set()
            for prod in captured_products:
                key = f"{prod.get('title')}|{prod.get('price')}|{prod.get('image')}"
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(prod)

            log.info(
                "playwright_fetch",
                url=url,
                status=status,
                proxy=bool(proxy),
                api_products=len(deduped),
                html_len=len(html),
            )
            await context.close()
            return status, html, warnings, deduped[: settings.max_products]
        finally:
            await browser.close()


class HybridScraper:
    """Async context manager opcional; también usable como instancia simple."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def __aenter__(self) -> HybridScraper:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def fetch(self, url: str, *, force_browser: bool = False) -> FetchResult:
        warnings: list[str] = []
        blocked = False

        # 1) Adaptadores de catálogo (Coto ofertas, etc.)
        catalog_products = await try_fetch_catalog_products(
            url, limit=self.settings.max_products, settings=self.settings
        )
        if catalog_products:
            warnings.append(f"Catálogo vía API del sitio ({len(catalog_products)} productos)")
            empty = CleanDocument(markdown="", image_urls=[], title=None, pruned_html_chars=0)
            doc = _merge_products_into_document(
                empty,
                catalog_products,
                max_chars=self.settings.max_markdown_chars,
            )
            return FetchResult(
                url=url,
                html="",
                method="api",
                status_code=200,
                document=doc,
                warnings=warnings,
                blocked=False,
                structured_products=catalog_products,
            )

        if not force_browser:
            try:
                status, html, w, final_url = await _fetch_httpx(url, self.settings)
                warnings.extend(w)
                doc = html_to_clean_document(
                    html,
                    url,
                    max_chars=self.settings.max_markdown_chars,
                    max_images=self.settings.max_products,
                )
                if looks_like_block(status, html, final_url):
                    warnings.append("Bloqueo/CAPTCHA detectado en httpx; escalando a Playwright")
                    force_browser = True
                else:
                    # Adapter genérico con hint del HTML (detecta VTEX/Shopify aunque el DOM esté vacío)
                    hinted = await try_fetch_catalog_products(
                        url,
                        limit=self.settings.max_products,
                        settings=self.settings,
                        html_hint=html,
                    )
                    jsonld_products = extract_products_from_jsonld(
                        html,
                        base_url=url,
                        limit=self.settings.max_products,
                    )
                    structured = hinted or jsonld_products
                    if structured:
                        source = "API (detectada por HTML)" if hinted else "JSON-LD"
                        warnings.append(
                            f"Productos desde {source} ({len(structured)} ítems)"
                        )
                        doc = _merge_products_into_document(
                            doc,
                            structured,
                            max_chars=self.settings.max_markdown_chars,
                        )
                        return FetchResult(
                            url=url,
                            html=html,
                            method="api" if hinted else "httpx",
                            status_code=status,
                            document=doc,
                            warnings=warnings,
                            blocked=False,
                            structured_products=structured,
                        )

                    useful_chars = len(
                        doc.markdown.replace("Detected product images", "")
                        .replace("[...truncated for LLM context budget...]", "")
                        .strip()
                    )
                    if looks_like_js_heavy(html, doc.markdown) or useful_chars < 300:
                        warnings.append(
                            "Contenido JS-heavy / poco texto útil; escalando a Playwright"
                        )
                        force_browser = True
                    else:
                        return FetchResult(
                            url=url,
                            html=html,
                            method="httpx",
                            status_code=status,
                            document=doc,
                            warnings=warnings,
                            blocked=False,
                        )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"httpx falló ({exc}); escalando a Playwright")
                force_browser = True

        status, html, w, api_products = await _fetch_playwright(url, self.settings)
        warnings.extend(w)
        if looks_like_block(status, html, url):
            blocked = True
            warnings.append(
                "Posible CAPTCHA/WAF tras Playwright. Configurá PROXY_LIST o revisá el sitio."
            )

        doc = html_to_clean_document(
            html,
            url,
            max_chars=self.settings.max_markdown_chars,
            max_images=self.settings.max_products,
        )
        if api_products:
            warnings.append(f"Productos capturados de XHR/JSON: {len(api_products)}")
            doc = _merge_products_into_document(
                doc,
                api_products,
                max_chars=self.settings.max_markdown_chars,
            )
        else:
            hinted = await try_fetch_catalog_products(
                url,
                limit=self.settings.max_products,
                settings=self.settings,
                html_hint=html,
            )
            jsonld_products = extract_products_from_jsonld(
                html,
                base_url=url,
                limit=self.settings.max_products,
            )
            api_products = hinted or jsonld_products
            if api_products:
                source = "API (detectada por HTML)" if hinted else "JSON-LD"
                warnings.append(f"Productos desde {source} ({len(api_products)} ítems)")
                doc = _merge_products_into_document(
                    doc,
                    api_products,
                    max_chars=self.settings.max_markdown_chars,
                )

        # Si el DOM quedó vacío pero hay productos API, no marcar como fallo
        useful = len(doc.markdown.strip())
        if useful < 80 and not api_products:
            warnings.append("Página casi vacía tras render; el sitio puede requerir login/proxy")

        return FetchResult(
            url=url,
            html=html,
            method="playwright",
            status_code=status,
            document=doc,
            warnings=warnings,
            blocked=blocked,
            structured_products=api_products,
        )


async def fetch_page(url: str, settings: Settings | None = None) -> FetchResult:
    async with HybridScraper(settings) as scraper:
        return await scraper.fetch(url)
