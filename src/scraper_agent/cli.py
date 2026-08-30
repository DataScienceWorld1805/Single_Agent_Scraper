"""CLI del Single Agent Scraper."""

from __future__ import annotations

import asyncio
import json
import webbrowser
from pathlib import Path
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
        help="groq | gemini | openai | anthropic | mistral | deepseek | openrouter | ollama",
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
        help="No guardar JSON / informe en OUTPUT_DIR",
    ),
    open_report: bool = typer.Option(
        True,
        "--open/--no-open",
        help="Abrir el informe HTML en el navegador al terminar",
    ),
) -> None:
    """Scrapea una URL, genera informe web + PDF e imprime un resumen."""
    from scraper_agent.agent import scrape
    from scraper_agent.config import get_settings
    from scraper_agent.models import GenericPage, ProductListing
    from scraper_agent.providers import LLM_PROVIDERS

    settings = get_settings()
    setup_logging(settings.log_level)

    response_model = ProductListing if schema == "products" else GenericPage
    chosen_provider = None
    if provider:
        if provider not in LLM_PROVIDERS:
            console.print(f"[red]--provider debe ser uno de: {', '.join(LLM_PROVIDERS)}[/red]")
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

        html_path: Path | None = None
        pdf_path: Path | None = None
        if not no_persist and isinstance(result.data, ProductListing):
            # generate_reports ya corre dentro de scrape; localizar el último informe
            reports_root = Path(settings.output_dir) / "reports"
            if reports_root.exists():
                candidates = sorted(
                    reports_root.glob("*/index.html"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    html_path = candidates[0]
                    pdf_candidate = html_path.parent / "informe.pdf"
                    if pdf_candidate.exists():
                        pdf_path = pdf_candidate

        # Resumen legible (no dump JSON crudo)
        console.print("\n[bold green]Scrape listo[/bold green]")
        if hasattr(result.data, "items"):
            console.print(f"Productos: [bold]{len(result.data.items)}[/bold]")  # type: ignore[arg-type]
            for i, item in enumerate(result.data.items[:8], start=1):  # type: ignore[attr-defined]
                price = item.price if item.price is not None else "—"
                cur = item.currency or ""
                console.print(f"  {i}. {item.title} — {price} {cur}")
        if html_path:
            console.print(f"\nInforme web: [cyan]{html_path.resolve()}[/cyan]")
        if pdf_path:
            console.print(f"PDF:         [cyan]{pdf_path.resolve()}[/cyan]")
        if open_report and html_path and html_path.exists():
            webbrowser.open(html_path.resolve().as_uri())

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("report")
def report_cmd(
    json_file: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON de scrape en output/*.json",
    ),
    open_report: bool = typer.Option(
        True,
        "--open/--no-open",
        help="Abrir el HTML en el navegador",
    ),
    no_pdf: bool = typer.Option(False, "--no-pdf", help="Solo generar HTML"),
) -> None:
    """Regenera informe HTML + PDF desde un JSON existente."""
    from scraper_agent.reporter import generate_reports_from_json

    async def _run() -> None:
        paths = await generate_reports_from_json(json_file, make_pdf=not no_pdf)
        console.print(f"[green]HTML:[/green] {paths.html_path.resolve()}")
        if paths.pdf_path.exists():
            console.print(f"[green]PDF:[/green]  {paths.pdf_path.resolve()}")
        if open_report:
            webbrowser.open(paths.html_path.resolve().as_uri())

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
