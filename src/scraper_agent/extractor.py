"""Fase 2: extracción estructurada con Instructor + autocorrección Pydantic."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from scraper_agent.config import LlmProvider, Settings, get_settings
from scraper_agent.logging_setup import get_logger
from scraper_agent.providers import get_instructor_client

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


SYSTEM_PROMPT = """Sos un extractor de datos web experto y preciso.
Tu única tarea es convertir el contenido Markdown de una página en JSON que cumpla EXACTAMENTE el schema.
Reglas:
- No inventes productos, precios ni imágenes que no estén en el contenido.
- Si un campo opcional no aparece, usá null / omitilo según el schema.
- Precios: número puro (sin símbolo). Moneda en el campo currency.
- Imágenes: el campo se llama images y es una LISTA de objetos con claves url y alt.
  NUNCA uses image_url ni image. Si hay foto, poné al menos una entrada en images con url absoluta.
- URL del producto: campo url (no product_url). Usá URL corta sin query ni fragmento.
- Para listados (Mercado Libre, eBay, etc.) extraé ítems visibles con título, precio, descripción e imagen.
- Limitá a máximo 6 productos para no truncar el JSON.
- Respondé solo con datos del schema; sin texto extra.
"""


def _build_user_prompt(
    *,
    url: str,
    goal: str | None,
    markdown: str,
    image_urls: list[str],
    validation_error: str | None = None,
    previous_output: str | None = None,
) -> str:
    parts = [
        f"URL fuente: {url}",
        f"Objetivo de scraping: {goal or 'Extraer la información principal de la página de forma estructurada.'}",
        "",
        "Contenido Markdown podado de la página:",
        "-----",
        markdown,
        "-----",
    ]
    if image_urls:
        parts.extend(
            [
                "",
                f"Image URLs detectadas en el DOM ({min(len(image_urls), 15)} de {len(image_urls)}):",
                *[f"- {u}" for u in image_urls[:15]],
            ]
        )
    if validation_error:
        parts.extend(
            [
                "",
                "El intento anterior FALLÓ la validación Pydantic. Corregí el JSON.",
                f"Error de validación:\n{validation_error}",
            ]
        )
        if previous_output:
            parts.extend(["", "Salida previa inválida:", previous_output])
    return "\n".join(parts)


class StructuredExtractor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def extract(
        self,
        *,
        url: str,
        markdown: str,
        image_urls: list[str],
        response_model: type[T],
        goal: str | None = None,
        provider: str | None = None,
    ) -> T:
        from typing import cast

        chosen = cast(LlmProvider | None, provider)
        client, model, used_provider = get_instructor_client(chosen, self.settings)
        max_retries = self.settings.max_extraction_retries

        validation_error: str | None = None
        previous_output: str | None = None
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            user_prompt = _build_user_prompt(
                url=url,
                goal=goal,
                markdown=markdown,
                image_urls=image_urls,
                validation_error=validation_error,
                previous_output=previous_output,
            )
            log.info(
                "llm_extract_attempt",
                attempt=attempt,
                provider=used_provider,
                model=model,
                schema=response_model.__name__,
            )
            try:
                # Instructor sync client; ejecutamos en thread para no bloquear el loop
                import asyncio

                def _call() -> T:
                    return client.chat.completions.create(
                        model=model,
                        response_model=response_model,
                        max_retries=0,  # autocorrección la manejamos nosotros
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                result = await asyncio.to_thread(_call)
                # Inyectar source_url si el modelo lo tiene vacío
                if hasattr(result, "source_url") and not getattr(result, "source_url", None):
                    result.source_url = url  # type: ignore[attr-defined]
                return result
            except ValidationError as exc:
                last_exc = exc
                validation_error = str(exc)
                previous_output = None
                log.warning("pydantic_validation_failed", attempt=attempt, error=validation_error)
            except Exception as exc:  # noqa: BLE001
                # Instructor a veces envuelve ValidationError
                last_exc = exc
                validation_error = str(exc)
                previous_output = getattr(exc, "value", None)
                if previous_output is not None and not isinstance(previous_output, str):
                    try:
                        previous_output = previous_output.model_dump_json()  # type: ignore[union-attr]
                    except Exception:  # noqa: BLE001
                        previous_output = str(previous_output)
                log.warning(
                    "llm_extract_failed",
                    attempt=attempt,
                    error=validation_error[:500],
                )

        raise RuntimeError(
            f"Extracción falló tras {max_retries} intentos de autocorrección: {last_exc}"
        )
