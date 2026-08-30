"""CLI del Single Agent Scraper."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console

from scraper_agent.logging_setup import setup_logging

app = typer.Typer(
    name="scraper-agent",
    help="Single Agent Web Scraper — fetch híbrido + extracción LLM (Groq/Gemini)",
    add_completion=False,
)
console = Console()


@app.command("scrape")
def scrape_cmd(
    url: str = typer.Argument(..., help="URL objetivo a scrapear"),
    goal: Optional[str] = typer.Option(
        None,
        "--goal",
        "-g",
        help="Objetivo en lenguaje natural (ej: productos con foto precio descripción)",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="groq | gemini (default: LLM_PROVIDER del .env)",
    ),
    download_images: bool = typer.Option(
        False,
        "--download-images",
        "-i",
        help="Descargar fotos de producto a OUTPUT_DIR/images",
    ),
    schema: str = typer.Option(
        "products",
        "--schema",
        "-s",
        help="products | generic",
    ),
    no_persist: bool = typer.Option(
        False,
        "--no-persist",
        help="No guardar JSON en OUTPUT_DIR",
    ),
) -> None:
    """Scrapea una URL y imprime JSON estructurado."""
    from scraper_agent.agent import scrape
    from scraper_agent.config import get_settings
    from scraper_agent.models import GenericPage, ProductListing

    settings = get_settings()
    setup_logging(settings.log_level)

    response_model = ProductListing if schema == "products" else GenericPage
    chosen_provider = None
    if provider:
        if provider not in {"groq", "gemini"}:
            console.print("[red]--provider debe ser groq o gemini[/red]")
            raise typer.Exit(code=2)
        chosen_provider = provider  # type: ignore[assignment]

    async def _run() -> None:
        result = await scrape(
            url,
            goal=goal,
            response_model=response_model,
            download_images=download_images or None,
            provider=chosen_provider,
            settings=settings,
            persist=not no_persist,
        )
        payload = json.loads(result.to_json())
        console.print_json(data=payload)

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.callback()
def main() -> None:
    """Entry point del paquete."""
    return None


if __name__ == "__main__":
    app()
