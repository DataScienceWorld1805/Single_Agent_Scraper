"""Interfaz web simple para pegar URL y scrapear."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, HttpUrl

from scraper_agent.agent import scrape
from scraper_agent.config import get_settings
from scraper_agent.logging_setup import setup_logging
from scraper_agent.models import ProductListing

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
_REPORT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

app = FastAPI(title="Single Agent Scraper", version="1.0.0")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Preparar output y servir informes estáticos
_settings_boot = get_settings()
_output_boot = Path(_settings_boot.output_dir).resolve()
_output_boot.mkdir(parents=True, exist_ok=True)
(_output_boot / "reports").mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(_output_boot)), name="files")


class ScrapeRequest(BaseModel):
    url: HttpUrl
    goal: str = Field(
        default="productos con foto precio descripcion",
        max_length=500,
    )
    provider: Literal["groq", "gemini"] = "groq"
    download_images: bool = True


def _ensure_output_mount() -> Path:
    settings = get_settings()
    output = Path(settings.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "reports").mkdir(parents=True, exist_ok=True)
    return output


def _list_recent_reports(limit: int = 8) -> list[dict[str, Any]]:
    reports_root = _ensure_output_mount() / "reports"
    if not reports_root.exists():
        return []
    items: list[dict[str, Any]] = []
    for html_path in sorted(
        reports_root.glob("*/index.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]:
        folder = html_path.parent
        pdf = folder / "informe.pdf"
        items.append(
            {
                "name": folder.name,
                "html_url": f"/files/reports/{folder.name}/index.html",
                "pdf_url": f"/files/reports/{folder.name}/informe.pdf" if pdf.exists() else None,
                "mtime": html_path.stat().st_mtime,
            }
        )
    return items


def _safe_report_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or not _REPORT_NAME_RE.fullmatch(cleaned) or ".." in cleaned:
        raise HTTPException(status_code=400, detail="Nombre de informe inválido")
    return cleaned


def _delete_local_images_from_json(json_path: Path, output: Path) -> int:
    """Borra archivos de imagen referenciados en el JSON del scrape."""
    if not json_path.is_file():
        return 0
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0

    deleted = 0
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0

    images_root = (output / "images").resolve()
    for item in items:
        if not isinstance(item, dict):
            continue
        for image in item.get("images") or []:
            if not isinstance(image, dict):
                continue
            local = image.get("local_path")
            if not isinstance(local, str) or not local.strip():
                continue
            path = Path(local)
            if not path.is_absolute():
                path = (output / local).resolve()
            else:
                path = path.resolve()
            try:
                path.relative_to(images_root)
            except ValueError:
                continue
            if path.is_file():
                path.unlink(missing_ok=True)
                deleted += 1
    return deleted


def _cleanup_empty_image_dirs(output: Path) -> None:
    images_root = output / "images"
    if not images_root.is_dir():
        return
    for folder in sorted(images_root.rglob("*"), reverse=True):
        if folder.is_dir():
            try:
                next(folder.iterdir())
            except StopIteration:
                folder.rmdir()
            except OSError:
                pass


def _delete_report_artifacts(name: str, *, output: Path) -> dict[str, Any]:
    report_dir = (output / "reports" / name).resolve()
    reports_root = (output / "reports").resolve()
    try:
        report_dir.relative_to(reports_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ruta de informe inválida") from exc

    removed: list[str] = []
    images_deleted = 0

    json_path = output / f"{name}.json"
    if json_path.is_file():
        images_deleted = _delete_local_images_from_json(json_path, output)
        json_path.unlink(missing_ok=True)
        removed.append(str(json_path.relative_to(output)))

    if report_dir.is_dir():
        shutil.rmtree(report_dir)
        removed.append(f"reports/{name}")

    _cleanup_empty_image_dirs(output)
    return {
        "name": name,
        "removed": removed,
        "images_deleted": images_deleted,
    }


@app.on_event("startup")
def on_startup() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    _ensure_output_mount()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_provider": settings.llm_provider,
            "reports": _list_recent_reports(),
        },
    )


@app.get("/api/reports")
async def api_list_reports() -> dict[str, Any]:
    return {"ok": True, "reports": _list_recent_reports()}


@app.delete("/api/reports/{name}")
async def api_delete_report(name: str) -> dict[str, Any]:
    safe_name = _safe_report_name(name)
    output = _ensure_output_mount()
    report_dir = output / "reports" / safe_name
    json_path = output / f"{safe_name}.json"
    if not report_dir.exists() and not json_path.exists():
        raise HTTPException(status_code=404, detail="Informe no encontrado")
    info = _delete_report_artifacts(safe_name, output=output)
    return {"ok": True, **info, "reports": _list_recent_reports()}


@app.delete("/api/reports")
async def api_delete_all_reports() -> dict[str, Any]:
    output = _ensure_output_mount()
    reports_root = output / "reports"
    names = sorted({p.parent.name for p in reports_root.glob("*/index.html")})
    # También JSON huérfanos con el mismo patrón de stem
    for json_path in output.glob("*.json"):
        if json_path.stem not in names:
            names.append(json_path.stem)

    deleted: list[dict[str, Any]] = []
    for name in names:
        if not _REPORT_NAME_RE.fullmatch(name):
            continue
        deleted.append(_delete_report_artifacts(name, output=output))

    # Si no quedan informes, limpiar carpeta de imágenes completa
    if not list(reports_root.glob("*/index.html")):
        images_root = output / "images"
        if images_root.is_dir():
            shutil.rmtree(images_root, ignore_errors=True)
            images_root.mkdir(parents=True, exist_ok=True)

    return {
        "ok": True,
        "deleted_count": len(deleted),
        "deleted": deleted,
        "reports": _list_recent_reports(),
    }


@app.post("/api/scrape")
async def api_scrape(payload: ScrapeRequest) -> dict[str, Any]:
    settings = get_settings()
    url = str(payload.url)

    try:
        result = await scrape(
            url,
            goal=payload.goal,
            response_model=ProductListing,
            download_images=payload.download_images,
            provider=payload.provider,
            settings=settings,
            persist=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    reports_root = Path(settings.output_dir) / "reports"
    html_url: str | None = None
    pdf_url: str | None = None
    report_name: str | None = None

    if reports_root.exists():
        candidates = sorted(
            reports_root.glob("*/index.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            folder = candidates[0].parent
            report_name = folder.name
            html_url = f"/files/reports/{folder.name}/index.html"
            if (folder / "informe.pdf").exists():
                pdf_url = f"/files/reports/{folder.name}/informe.pdf"

    items_preview: list[dict[str, Any]] = []
    if isinstance(result.data, ProductListing):
        for item in result.data.items[:12]:
            img = item.images[0].url if item.images else None
            items_preview.append(
                {
                    "title": item.title,
                    "price": item.price,
                    "currency": item.currency,
                    "image": img,
                }
            )

    items_count = (
        len(result.data.items) if isinstance(result.data, ProductListing) else 0
    )
    return {
        "ok": True,
        "source_url": url,
        "items_count": items_count,
        "fetch_method": result.fetch_method,
        "blocked": result.blocked,
        "warnings": result.warnings,
        "report_name": report_name,
        "html_url": html_url,
        "pdf_url": pdf_url,
        "items": items_preview,
    }


def create_app() -> FastAPI:
    return app
