"""Ejemplo: scrapear un listado/producto estilo marketplace."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Permite ejecutar sin instalar el paquete: python examples/scrape_products.py <url>
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scraper_agent.agent import scrape
from scraper_agent.models import ProductListing


async def main(url: str) -> None:
    result = await scrape(
        url,
        goal="Extraer productos con foto, precio y descripción",
        response_model=ProductListing,
        download_images=True,
        persist=True,
    )
    print(result.to_json())


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    asyncio.run(main(target))
