"""Descarga opcional de imágenes de producto a disco."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from scraper_agent.config import Settings, get_settings
from scraper_agent.logging_setup import get_logger
from scraper_agent.models import ProductItem, ProductListing, ScrapedImage

log = get_logger(__name__)


def _slug(text: str, *, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower()).strip("-")
    return (cleaned or "item")[:max_len]


def _extension_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return suffix
    return ".jpg"


async def download_image(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    semaphore: asyncio.Semaphore,
) -> str | None:
    if url.startswith("data:"):
        return None
    async with semaphore:
        try:
            response = await client.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
            return str(dest)
        except Exception as exc:  # noqa: BLE001
            log.warning("image_download_failed", url=url, error=str(exc))
            return None


async def attach_local_images(
    listing: ProductListing,
    *,
    settings: Settings | None = None,
) -> ProductListing:
    cfg = settings or get_settings()
    base = Path(cfg.output_dir) / "images" / _slug(urlparse(listing.source_url).netloc or "site")
    semaphore = asyncio.Semaphore(cfg.image_download_concurrency)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        tasks: list[tuple[ScrapedImage, asyncio.Task[str | None]]] = []
        for idx, item in enumerate(listing.items):
            item_dir = base / f"{idx:03d}_{_slug(item.title)}"
            for img_idx, image in enumerate(item.images):
                digest = hashlib.sha1(image.url.encode()).hexdigest()[:10]
                dest = item_dir / f"{img_idx:02d}_{digest}{_extension_from_url(image.url)}"
                task = asyncio.create_task(download_image(client, image.url, dest, semaphore))
                tasks.append((image, task))

        for image, task in tasks:
            local = await task
            if local:
                image.local_path = local

    log.info("images_downloaded", count=sum(1 for i in listing.items for im in i.images if im.local_path))
    return listing


async def download_images_from_items(
    items: list[ProductItem],
    source_url: str,
    *,
    settings: Settings | None = None,
) -> list[ProductItem]:
    listing = ProductListing(source_url=source_url, items=items)
    updated = await attach_local_images(listing, settings=settings)
    return updated.items
