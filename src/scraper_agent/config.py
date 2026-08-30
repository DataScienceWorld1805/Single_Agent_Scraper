"""Settings del agente vía pydantic-settings y variables de entorno."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

_PLACEHOLDER_PREFIXES = (
    "gsk_your",
    "your_",
    "sk-your",
    "sk-ant-your",
    "ollama_local",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: LlmProvider = "groq"

    # Groq (sin cambios de defaults)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # Gemini (sin cambios de defaults)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    # Anthropic (Claude)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # Mistral
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # OpenRouter (agregador)
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4.1-mini"

    # Ollama (local)
    ollama_api_key: str = "ollama"
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"

    http_timeout: float = 30.0
    playwright_timeout: int = 45_000
    max_fetch_retries: int = 3
    max_extraction_retries: int = 3
    max_markdown_chars: int = 12_000
    max_products: int = Field(default=40, ge=1, le=500)

    proxy_list: str = ""
    download_images: bool = False
    output_dir: str = "./output"
    image_download_concurrency: int = 5
    log_level: str = "INFO"

    user_agent_pool: list[str] = Field(
        default_factory=lambda: [
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
                "Gecko/20100101 Firefox/122.0"
            ),
        ]
    )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    def proxies(self) -> list[str]:
        if not self.proxy_list.strip():
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]

    def resolve_model(self, provider: LlmProvider | None = None) -> str:
        chosen = provider or self.llm_provider
        mapping = {
            "groq": self.groq_model,
            "gemini": self.gemini_model,
            "openai": self.openai_model,
            "anthropic": self.anthropic_model,
            "mistral": self.mistral_model,
            "deepseek": self.deepseek_model,
            "openrouter": self.openrouter_model,
            "ollama": self.ollama_model,
        }
        return mapping[chosen]

    def require_api_key(self, provider: LlmProvider | None = None) -> str:
        chosen = provider or self.llm_provider
        key_map: dict[LlmProvider, tuple[str, str]] = {
            "groq": (self.groq_api_key, "GROQ_API_KEY"),
            "gemini": (self.gemini_api_key, "GEMINI_API_KEY"),
            "openai": (self.openai_api_key, "OPENAI_API_KEY"),
            "anthropic": (self.anthropic_api_key, "ANTHROPIC_API_KEY"),
            "mistral": (self.mistral_api_key, "MISTRAL_API_KEY"),
            "deepseek": (self.deepseek_api_key, "DEEPSEEK_API_KEY"),
            "openrouter": (self.openrouter_api_key, "OPENROUTER_API_KEY"),
            "ollama": (self.ollama_api_key or "ollama", "OLLAMA_API_KEY"),
        }
        raw, env_name = key_map[chosen]
        if chosen == "ollama":
            return raw or "ollama"
        if not raw or raw.lower().startswith(_PLACEHOLDER_PREFIXES) or raw.startswith("your_"):
            raise ValueError(
                f"{env_name} no configurada. Copiá .env.example a .env y pegá tu key."
            )
        return raw


@lru_cache
def get_settings() -> Settings:
    return Settings()
