"""Factory de clientes Instructor para múltiples proveedores LLM."""

from __future__ import annotations

from typing import Any, Literal

import instructor
from openai import OpenAI

from scraper_agent.config import Settings, get_settings
from scraper_agent.logging_setup import get_logger

log = get_logger(__name__)

LlmProvider = Literal[
    "groq",
    "gemini",
    "openai",
    "anthropic",
    "mistral",
    "deepseek",
    "openrouter",
    "ollama",
]

LLM_PROVIDERS: tuple[LlmProvider, ...] = (
    "groq",
    "gemini",
    "openai",
    "anthropic",
    "mistral",
    "deepseek",
    "openrouter",
    "ollama",
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"


def get_instructor_client(
    provider: LlmProvider | None = None,
    settings: Settings | None = None,
) -> tuple[Any, str, LlmProvider]:
    """
    Retorna (client_instructor, model_name, provider_usado).

    Groq / OpenAI / Mistral / DeepSeek / OpenRouter / Ollama: API OpenAI-compatible.
    Gemini: google-genai + instructor.
    Anthropic: SDK Anthropic + instructor.
    """
    cfg = settings or get_settings()
    chosen: LlmProvider = provider or cfg.llm_provider  # type: ignore[assignment]
    if chosen not in LLM_PROVIDERS:
        raise ValueError(f"Proveedor LLM no soportado: {chosen}. Usá uno de {LLM_PROVIDERS}")

    model = cfg.resolve_model(chosen)
    api_key = cfg.require_api_key(chosen)

    if chosen == "groq":
        raw = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="groq", model=model)
        return client, model, "groq"

    if chosen == "gemini":
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Instalá google-genai para usar Gemini") from exc

        gemini_client = genai.Client(api_key=api_key)
        try:
            client = instructor.from_genai(
                gemini_client, mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS
            )
        except Exception:
            client = instructor.from_genai(gemini_client)
        log.info("llm_client_ready", provider="gemini", model=model)
        return client, model, "gemini"

    if chosen == "openai":
        raw = OpenAI(api_key=api_key)
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="openai", model=model)
        return client, model, "openai"

    if chosen == "anthropic":
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Instalá anthropic para usar Claude") from exc
        raw = Anthropic(api_key=api_key)
        client = instructor.from_anthropic(raw)
        log.info("llm_client_ready", provider="anthropic", model=model)
        return client, model, "anthropic"

    if chosen == "mistral":
        raw = OpenAI(api_key=api_key, base_url=MISTRAL_BASE_URL)
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="mistral", model=model)
        return client, model, "mistral"

    if chosen == "deepseek":
        raw = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="deepseek", model=model)
        return client, model, "deepseek"

    if chosen == "openrouter":
        raw = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://localhost:8000",
                "X-Title": "Single Agent Scraper",
            },
        )
        client = instructor.from_openai(raw)
        log.info("llm_client_ready", provider="openrouter", model=model)
        return client, model, "openrouter"

    # ollama
    base_url = (cfg.ollama_base_url or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    raw = OpenAI(api_key=api_key or "ollama", base_url=base_url)
    client = instructor.from_openai(raw)
    log.info("llm_client_ready", provider="ollama", model=model, base_url=base_url)
    return client, model, "ollama"
