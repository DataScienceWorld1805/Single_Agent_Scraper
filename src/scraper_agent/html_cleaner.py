"""Poda de DOM (Selectolax) + extracción de main content (Trafilatura)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import trafilatura
from selectolax.parser import HTMLParser

from scraper_agent.logging_setup import get_logger

log = get_logger(__name__)

STRIP_TAGS = (
    "script",
    "style",
    "svg",
    "noscript",
    "iframe",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "button",
    "template",
    "link",
    "meta",
)


def _strip_tracking(url: str) -> str:
    """Quita fragmento y query tracking de URLs de producto."""
    parsed = urlparse(url)
    # Mantener query solo si es corta y no es tracking típico de ML
    query = parsed.query
    if query and (
        "polycard" in query
        or "reco_" in query
        or "c_id=" in query
        or len(query) > 80
    ):
        query = ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


@dataclass
class CleanDocument:
    markdown: str
    image_urls: list[str] = field(default_factory=list)
    title: str | None = None
    pruned_html_chars: int = 0


def _absolutize(base_url: str, src: str) -> str | None:
    src = (src or "").strip()
    if not src or src.startswith("data:"):
        return None
    if src.startswith("//"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}:{src}"
    return urljoin(base_url, src)


def _best_from_srcset(srcset: str) -> str | None:
    """Toma el último candidato del srcset (suele ser el de mayor resolución)."""
    parts = [p.strip() for p in srcset.split(",") if p.strip()]
    if not parts:
        return None
    return parts[-1].split()[0]


def prune_html(html: str) -> str:
    if not html or not html.strip():
        return ""
    tree = HTMLParser(html)
    if tree.body is None and not tree.html:
        return html
    for tag in STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    # Nodos típicos de tracking / cookie banners
    for selector in (
        "[class*='cookie']",
        "[id*='cookie']",
        "[class*='advert']",
        "[id*='ads']",
    ):
        for node in tree.css(selector):
            node.decompose()
    body = tree.body
    if body is not None and body.html:
        return body.html
    return tree.html or html


def extract_images(html: str, base_url: str, *, limit: int = 40) -> list[str]:
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []

    for img in tree.css("img"):
        candidates = [
            img.attributes.get("src"),
            img.attributes.get("data-src"),
            img.attributes.get("data-lazy-src"),
            img.attributes.get("data-original"),
        ]
        srcset = img.attributes.get("srcset") or img.attributes.get("data-srcset")
        if srcset:
            candidates.append(_best_from_srcset(srcset))

        for raw in candidates:
            if not raw:
                continue
            abs_url = _absolutize(base_url, raw)
            if not abs_url or abs_url in seen:
                continue
            # Filtrar tracking pixels diminutos / placeholders comunes
            lower = abs_url.lower()
            if any(x in lower for x in ("1x1", "pixel", "spacer", "blank.gif")):
                continue
            seen.add(abs_url)
            urls.append(_strip_tracking(abs_url))
            if len(urls) >= limit:
                return urls
    return urls


def html_to_clean_document(
    html: str,
    base_url: str,
    *,
    max_chars: int = 40_000,
) -> CleanDocument:
    pruned = prune_html(html)
    image_urls = extract_images(html, base_url)

    downloaded = trafilatura.extract(
        pruned,
        url=base_url,
        include_comments=False,
        include_tables=True,
        include_links=True,
        include_images=True,
        output_format="markdown",
        favor_recall=True,
    )

    title = trafilatura.extract_metadata(pruned)
    page_title = title.title if title else None

    markdown = downloaded or ""
    if not markdown.strip():
        # Fallback: texto plano del DOM podado
        tree = HTMLParser(pruned)
        markdown = tree.text(separator="\n", strip=True)

    if image_urls:
        img_block = "\n".join(f"- {u}" for u in image_urls[:15])
        markdown = f"{markdown}\n\n## Detected product images\n{img_block}\n"

    original_len = len(markdown)
    if len(markdown) > max_chars:
        markdown = markdown[:max_chars] + "\n\n[...truncated for LLM context budget...]"
        log.info(
            "markdown_truncated",
            original_chars=original_len,
            max_chars=max_chars,
        )

    return CleanDocument(
        markdown=markdown,
        image_urls=image_urls,
        title=page_title,
        pruned_html_chars=len(pruned),
    )
