"""Single Agent Web Scraper — fetch híbrido + extracción LLM estructurada."""

from scraper_agent.agent import scrape
from scraper_agent.models import (
    GenericPage,
    ProductItem,
    ProductListing,
    ScrapedImage,
    ScrapedResult,
)

__all__ = [
    "scrape",
    "GenericPage",
    "ProductItem",
    "ProductListing",
    "ScrapedImage",
    "ScrapedResult",
]

__version__ = "1.0.0"
