"""Tests de modelos Pydantic."""

from scraper_agent.models import ProductItem, ProductListing, ScrapedImage, ScrapedResult


def test_product_listing_roundtrip() -> None:
    listing = ProductListing(
        source_url="https://www.mercadolibre.com.ar/algo",
        items=[
            ProductItem(
                title="Notebook",
                price=799999.0,
                currency="ARS",
                description="Ryzen 7, 16GB RAM",
                images=[ScrapedImage(url="https://http2.mlstatic.com/D_NQ_NP_1.jpg", alt="foto")],
            )
        ],
    )
    result = ScrapedResult(
        data=listing,
        fetch_method="playwright",
        warnings=[],
        raw_markdown_chars=1200,
        image_urls_detected=["https://http2.mlstatic.com/D_NQ_NP_1.jpg"],
    )
    payload = result.model_dump()
    assert payload["data"]["items"][0]["title"] == "Notebook"
    assert payload["fetch_method"] == "playwright"
    assert "Notebook" in result.to_json()
