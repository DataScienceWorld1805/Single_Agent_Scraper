"""Esquemas Pydantic estrictos para extracción estructurada."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field


class ScrapedImage(BaseModel):
    url: str = Field(..., description="URL absoluta de la imagen del producto")
    alt: str | None = Field(default=None, description="Texto alternativo si existe")
    local_path: str | None = Field(
        default=None,
        description="Ruta local si la imagen fue descargada a disco",
    )


class ProductItem(BaseModel):
    title: str = Field(..., description="Título o nombre del producto")
    price: float | None = Field(default=None, description="Precio numérico sin símbolo")
    currency: str | None = Field(
        default=None,
        description="Código o símbolo de moneda (ARS, USD, EUR, $)",
    )
    description: str | None = Field(
        default=None,
        description="Descripción limpia del producto",
    )
    images: list[ScrapedImage] = Field(
        default_factory=list,
        description="Fotos del producto (al menos la principal si existe)",
    )
    url: str | None = Field(default=None, description="URL del detalle del producto")
    availability: str | None = Field(
        default=None,
        description="Disponibilidad o stock si aparece en la página",
    )
    seller: str | None = Field(default=None, description="Vendedor si está visible")
    notes: str | None = Field(
        default=None,
        description="Notas adicionales relevantes detectadas en la página",
    )


class ProductListing(BaseModel):
    source_url: str = Field(..., description="URL scrapeada")
    items: list[ProductItem] = Field(
        default_factory=list,
        description="Lista de productos encontrados en la página",
    )
    page_title: str | None = None
    scraped_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp ISO del scrape; si falta, el agente lo completa",
    )


class GenericPage(BaseModel):
    source_url: str
    title: str | None = None
    main_content: str = Field(..., description="Contenido principal limpio")
    links: list[str] = Field(default_factory=list)
    images: list[ScrapedImage] = Field(default_factory=list)
    category: str | None = Field(default=None, description="Categoría o tipo de página si aplica")
    scraped_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


T = TypeVar("T", bound=BaseModel)


class ScrapedResult(BaseModel, Generic[T]):
    data: T
    fetch_method: Literal["httpx", "playwright"]
    warnings: list[str] = Field(default_factory=list)
    raw_markdown_chars: int = 0
    blocked: bool = False
    image_urls_detected: list[str] = Field(default_factory=list)

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


# Alias útil para tipado de schemas dinámicos
AnyScrapedModel = ProductListing | GenericPage
