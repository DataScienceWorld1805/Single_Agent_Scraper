"""Factory de clientes Instructor para Groq y Gemini."""

from __future__ import annotations

from typing import Any, Literal

import instructor
from openai import OpenAI

from scraper_agent.config import Settings, get_settings
from scraper_agent.logging_setup import get_logger

log = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def get_instructor_client(
    provider: Literal["groq", "gemini"] | None = None,
    settings: Settings | None = None,
) -> tuple[Any, str, Literal["groq", "gemini"]]:
    """
    Retorna (client_instructor, model_name, provider_usado).

    Groq usa API OpenAI-compatible.
    Gemini usa google-genai + instructor.
    """
    cfg = settings or get_settings()
    chosen: Literal["groq", "gemini"] = provider or cfg.llm_provider
    model = cfg.resolve_model(chosen)
    api_key = cfg.require_api_key(chosen)

    if chosen == "groq":
        raw = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="groq", model=model)
        return client, model, "groq"

    # Gemini
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Instalá google-genai para usar Gemini") from exc

    gemini_client = genai.Client(api_key=api_key)
    try:
        client = instructor.from_genai(gemini_client, mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS)
    except Exception:
        # Fallback a modo JSON genérico si la versión de instructor difiere
        client = instructor.from_genai(gemini_client)

    log.info("llm_client_ready", provider="gemini", model=model)
    return client, model, "gemini"
