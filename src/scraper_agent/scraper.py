"""Fase 1: fetch híbrido httpx → Playwright Stealth."""

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
    method: Literal["httpx", "playwright"]
    status_code: int
    document: CleanDocument
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False


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
                    # Reintentar con otro UA/proxy si hay más intentos
                    if attempt.retry_state.attempt_number < settings.max_fetch_retries:
                        proxy = pick_proxy(settings)
                        headers = build_headers(settings)
                        raise httpx.TransportError(f"retryable status {response.status_code}")
                # Decodificar de forma tolerante (ML a veces responde latin-1/windows-1252)
                html = response.content.decode(response.encoding or "utf-8", errors="replace")
                if not html.strip():
                    html = response.text
                return response.status_code, html, warnings, final_url

    raise FetchError("httpx agotó reintentos", blocked=True)


async def _fetch_playwright(url: str, settings: Settings) -> tuple[int, str, list[str]]:
    warnings: list[str] = []
    proxy = pick_proxy(settings)
    headers = build_headers(settings)
    proxy_cfg = playwright_proxy_config(proxy)

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
                viewport={"width": 1366, "height": 768},
                java_script_enabled=True,
                extra_http_headers={
                    k: v for k, v in headers.items() if k.lower() != "user-agent"
                },
            )
            page = await context.new_page()

            # Stealth best-effort (API varía según versión de playwright-stealth)
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

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.playwright_timeout,
            )
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=min(15_000, settings.playwright_timeout),
                )
            except Exception:  # noqa: BLE001
                warnings.append("networkidle timeout; se usa DOM actual")

            # Esperar señales de listado/producto (ML, eBay, genéricos)
            for selector in (
                "img[src*='D_Q_NP']",
                "img[src*='D_NQ_NP']",
                ".ui-search-layout",
                ".poly-card",
                "[data-testid='item']",
                "main img",
            ):
                try:
                    await page.wait_for_selector(selector, timeout=5_000)
                    break
                except Exception:  # noqa: BLE001
                    continue

            # Scroll ligero para lazy-load de imágenes (ML / eBay)
            try:
                await page.evaluate(
                    """async () => {
                        await new Promise(resolve => {
                            let total = 0;
                            const step = () => {
                                window.scrollBy(0, 600);
                                total += 600;
                                if (total >= document.body.scrollHeight || total > 4000) {
                                    resolve();
                                } else {
                                    setTimeout(step, 150);
                                }
                            };
                            step();
                        });
                    }"""
                )
            except Exception:  # noqa: BLE001
                pass

            html = await page.content()
            status = response.status if response else 200
            log.info("playwright_fetch", url=url, status=status, proxy=bool(proxy))
            await context.close()
            return status, html, warnings
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

        if not force_browser:
            try:
                status, html, w, final_url = await _fetch_httpx(url, self.settings)
                warnings.extend(w)
                doc = html_to_clean_document(
                    html,
                    url,
                    max_chars=self.settings.max_markdown_chars,
                )
                useful_chars = len(
                    doc.markdown.replace("Detected product images", "")
                    .replace("[...truncated for LLM context budget...]", "")
                    .strip()
                )
                if looks_like_block(status, html, final_url):
                    warnings.append("Bloqueo/CAPTCHA detectado en httpx; escalando a Playwright")
                    force_browser = True
                elif looks_like_js_heavy(html, doc.markdown) or useful_chars < 300:
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

        status, html, w = await _fetch_playwright(url, self.settings)
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
        )
        return FetchResult(
            url=url,
            html=html,
            method="playwright",
            status_code=status,
            document=doc,
            warnings=warnings,
            blocked=blocked,
        )


async def fetch_page(url: str, settings: Settings | None = None) -> FetchResult:
    async with HybridScraper(settings) as scraper:
        return await scraper.fetch(url)
