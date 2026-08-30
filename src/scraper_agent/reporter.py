"""Informes visuales: catálogo HTML + PDF descargable."""

from __future__ import annotations

import base64
import html
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scraper_agent.logging_setup import get_logger
from scraper_agent.models import ProductItem, ProductListing, ScrapedResult

log = get_logger(__name__)


@dataclass
class ReportPaths:
    html_path: Path
    pdf_path: Path
    report_dir: Path


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _format_price(price: float | None, currency: str | None) -> str:
    if price is None:
        return "—"
    cur = (currency or "").strip().upper()
    symbols = {"GBP": "£", "USD": "US$", "EUR": "€", "ARS": "$", "MXN": "MX$"}
    symbol = symbols.get(cur, f"{cur} " if cur else "")
    return f"{symbol}{price:,.2f}" + (f" {cur}" if cur in {"ARS", "MXN"} else "")


def _resolve_image_src(
    item: ProductItem,
    *,
    report_dir: Path,
    embed_base64: bool,
) -> str | None:
    for image in item.images:
        local = image.local_path
        if local:
            path = Path(local)
            if not path.is_absolute():
                # Rutas relativas al cwd del scrape (p.ej. output/images/...)
                path = Path.cwd() / path
            if path.exists():
                if embed_base64:
                    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    return f"data:{mime};base64,{encoded}"
                try:
                    rel = path.resolve().relative_to(report_dir.resolve())
                    return rel.as_posix()
                except ValueError:
                    # Copiar referencia via file URI relativa creando symlink/copy no; usar data URI
                    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    return f"data:{mime};base64,{encoded}"
        if image.url and not image.url.startswith("data:"):
            return image.url
    return None


def _product_cards_html(
    items: list[ProductItem],
    *,
    report_dir: Path,
    embed_base64: bool,
) -> str:
    cards: list[str] = []
    for idx, item in enumerate(items, start=1):
        src = _resolve_image_src(item, report_dir=report_dir, embed_base64=embed_base64)
        img_block = (
            f'<img src="{_esc(src)}" alt="{_esc(item.title)}" loading="lazy" />'
            if src
            else '<div class="img-fallback">Sin imagen</div>'
        )
        desc = item.description or item.notes or "Sin descripción disponible."
        link = (
            f'<a class="product-link" href="{_esc(item.url)}" target="_blank" rel="noopener">Ver producto</a>'
            if item.url
            else ""
        )
        avail = f'<span class="pill">{_esc(item.availability)}</span>' if item.availability else ""
        seller = f'<span class="meta">Vendedor: {_esc(item.seller)}</span>' if item.seller else ""
        cards.append(
            f"""
            <article class="card">
              <div class="thumb">{img_block}</div>
              <div class="body">
                <div class="top">
                  <span class="idx">#{idx}</span>
                  {avail}
                </div>
                <h2>{_esc(item.title)}</h2>
                <p class="price">{_esc(_format_price(item.price, item.currency))}</p>
                <p class="desc">{_esc(desc)}</p>
                {seller}
                {link}
              </div>
            </article>
            """
        )
    if not cards:
        return '<p class="empty">No se extrajeron productos en este scrape.</p>'
    return "\n".join(cards)


def build_report_html(
    result: ScrapedResult[ProductListing] | dict[str, Any],
    *,
    report_dir: Path,
    pdf_name: str,
    embed_images: bool = False,
) -> str:
    if isinstance(result, dict):
        scraped = ScrapedResult[ProductListing].model_validate(result)
    else:
        scraped = result

    listing = scraped.data
    if not isinstance(listing, ProductListing):
        listing = ProductListing.model_validate(listing)

    host = urlparse(listing.source_url).netloc or "scrape"
    scraped_at = listing.scraped_at or datetime.now(timezone.utc)
    if isinstance(scraped_at, str):
        scraped_label = scraped_at
    else:
        scraped_label = scraped_at.strftime("%Y-%m-%d %H:%M UTC")

    prices = [i.price for i in listing.items if i.price is not None]
    price_stats = ""
    if prices:
        cur = next((i.currency for i in listing.items if i.currency), "")
        price_stats = (
            f"<li><strong>Precio mín.</strong> {_esc(_format_price(min(prices), cur))}</li>"
            f"<li><strong>Precio máx.</strong> {_esc(_format_price(max(prices), cur))}</li>"
        )

    cards = _product_cards_html(
        listing.items,
        report_dir=report_dir,
        embed_base64=embed_images,
    )
    warnings = "".join(f"<li>{_esc(w)}</li>" for w in scraped.warnings) or "<li>Ninguna</li>"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Informe de scraping — {_esc(host)}</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --ink: #1c1915;
      --muted: #5c554c;
      --accent: #0f5c4c;
      --accent-2: #c45c26;
      --card: #fffcf7;
      --line: #d9d0c2;
      --shadow: 0 12px 30px rgba(28, 25, 21, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 92, 76, 0.08), transparent 40%),
        linear-gradient(180deg, #f7f3ea 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .wrap {{
      width: min(1100px, calc(100% - 2rem));
      margin: 0 auto;
      padding: 2rem 0 3.5rem;
    }}
    header.hero {{
      display: grid;
      gap: 1rem;
      padding: 1.6rem 1.8rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      margin-bottom: 1.5rem;
    }}
    .brand {{
      font-size: 0.85rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}
    h1 {{
      margin: 0.2rem 0 0.4rem;
      font-size: clamp(1.6rem, 3vw, 2.2rem);
      line-height: 1.15;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      max-width: 60ch;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 0.4rem;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.7rem 1.05rem;
      border-radius: 999px;
      text-decoration: none;
      font-weight: 650;
      border: 1px solid transparent;
    }}
    .btn-primary {{
      background: var(--accent);
      color: #f7fffb;
    }}
    .btn-secondary {{
      background: transparent;
      color: var(--ink);
      border-color: var(--line);
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 0.75rem;
      margin: 1.25rem 0 1.5rem;
      padding: 0;
      list-style: none;
    }}
    .stats li {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0.9rem 1rem;
      color: var(--muted);
    }}
    .stats strong {{
      display: block;
      color: var(--ink);
      margin-bottom: 0.2rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 1rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    .thumb {{
      aspect-ratio: 4 / 3;
      background: #ebe4d8;
      display: grid;
      place-items: center;
      overflow: hidden;
    }}
    .thumb img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .img-fallback {{
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .body {{
      padding: 1rem 1rem 1.15rem;
      display: grid;
      gap: 0.45rem;
    }}
    .top {{
      display: flex;
      justify-content: space-between;
      gap: 0.5rem;
      align-items: center;
    }}
    .idx {{
      font-size: 0.75rem;
      color: var(--muted);
      font-weight: 700;
    }}
    .pill {{
      font-size: 0.72rem;
      background: rgba(15, 92, 76, 0.1);
      color: var(--accent);
      padding: 0.2rem 0.5rem;
      border-radius: 999px;
      font-weight: 650;
    }}
    h2 {{
      margin: 0;
      font-size: 1.05rem;
      line-height: 1.3;
    }}
    .price {{
      margin: 0;
      font-size: 1.25rem;
      color: var(--accent-2);
      font-weight: 750;
    }}
    .desc, .meta {{
      margin: 0;
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.4;
    }}
    .product-link {{
      color: var(--accent);
      font-weight: 650;
      text-decoration: none;
      margin-top: 0.25rem;
    }}
    .footer-box {{
      margin-top: 1.75rem;
      padding: 1rem 1.2rem;
      border-radius: 14px;
      border: 1px dashed var(--line);
      background: rgba(255,252,247,0.7);
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .footer-box ul {{ margin: 0.4rem 0 0; padding-left: 1.1rem; }}
    .empty {{
      grid-column: 1 / -1;
      padding: 2rem;
      text-align: center;
      color: var(--muted);
      background: var(--card);
      border-radius: 14px;
      border: 1px solid var(--line);
    }}
    @media print {{
      body {{ background: white; }}
      .btn-secondary {{ display: none; }}
      header.hero {{ box-shadow: none; }}
      .card {{ box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div>
        <div class="brand">Single Agent Scraper</div>
        <h1>{_esc(listing.page_title or f"Informe de productos — {host}")}</h1>
        <p class="subtitle">
          Fuente: <a href="{_esc(listing.source_url)}">{_esc(listing.source_url)}</a><br />
          Generado: {_esc(scraped_label)} · Método: {_esc(scraped.fetch_method)}
          {" · <strong>Bloqueado</strong>" if scraped.blocked else ""}
        </p>
        <div class="actions">
          <a class="btn btn-primary" href="{_esc(pdf_name)}" download>Descargar PDF</a>
          <a class="btn btn-secondary" href="{_esc(listing.source_url)}" target="_blank" rel="noopener">Abrir sitio fuente</a>
        </div>
      </div>
    </header>

    <ul class="stats">
      <li><strong>Productos</strong> {len(listing.items)}</li>
      <li><strong>Fotos detectadas</strong> {len(scraped.image_urls_detected)}</li>
      {price_stats}
      <li><strong>Markdown analizado</strong> {scraped.raw_markdown_chars} chars</li>
    </ul>

    <section class="grid">
      {cards}
    </section>

    <aside class="footer-box">
      <strong>Warnings / notas técnicas</strong>
      <ul>{warnings}</ul>
    </aside>
  </div>
</body>
</html>
"""


async def render_pdf_from_html(html_path: Path, pdf_path: Path) -> Path:
    """Usa Chromium (Playwright) para imprimir el HTML a PDF."""
    from playwright.async_api import async_playwright

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            await page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "12mm", "right": "10mm", "bottom": "12mm", "left": "10mm"},
            )
        finally:
            await browser.close()
    return pdf_path


def _stem_from_json_or_url(json_path: Path | None, source_url: str) -> str:
    if json_path is not None:
        return json_path.stem
    host = urlparse(source_url).netloc.replace(":", "_") or "report"
    return f"{host}_report"


async def generate_reports(
    result: ScrapedResult[ProductListing] | dict[str, Any],
    *,
    output_dir: str | Path,
    json_path: Path | None = None,
    make_pdf: bool = True,
) -> ReportPaths:
    """
    Genera:
      - output/reports/<stem>/index.html  (vista web)
      - output/reports/<stem>/informe.pdf (descargable)
    """
    if isinstance(result, dict):
        scraped = ScrapedResult[ProductListing].model_validate(result)
    else:
        scraped = result

    listing = scraped.data
    if not isinstance(listing, ProductListing):
        listing = ProductListing.model_validate(listing)
        scraped = scraped.model_copy(update={"data": listing})

    out_root = Path(output_dir)
    stem = _stem_from_json_or_url(json_path, listing.source_url)
    report_dir = out_root / "reports" / stem
    report_dir.mkdir(parents=True, exist_ok=True)

    html_path = report_dir / "index.html"
    pdf_path = report_dir / "informe.pdf"
    pdf_name = pdf_path.name

    # HTML web: embebe imágenes en base64 para que funcione abriendo el archivo
    # sin depender de rutas relativas rotas a output/images.
    web_html = build_report_html(
        scraped,
        report_dir=report_dir,
        pdf_name=pdf_name,
        embed_images=True,
    )
    html_path.write_text(web_html, encoding="utf-8")
    log.info("report_html_written", path=str(html_path))

    if make_pdf:
        # HTML dedicado al PDF (también con embeds) para impresión fiable
        pdf_html_path = report_dir / "_print.html"
        pdf_html_path.write_text(web_html, encoding="utf-8")
        try:
            await render_pdf_from_html(pdf_html_path, pdf_path)
            log.info("report_pdf_written", path=str(pdf_path))
        finally:
            if pdf_html_path.exists():
                pdf_html_path.unlink(missing_ok=True)

    return ReportPaths(html_path=html_path, pdf_path=pdf_path, report_dir=report_dir)


def load_scrape_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def generate_reports_from_json(
    json_path: Path,
    *,
    make_pdf: bool = True,
) -> ReportPaths:
    payload = load_scrape_json(json_path)
    output_dir = json_path.parent
    return await generate_reports(
        payload,
        output_dir=output_dir,
        json_path=json_path,
        make_pdf=make_pdf,
    )
