"""Settings del agente vía pydantic-settings y variables de entorno."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["groq", "gemini"] = "groq"

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

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

    def resolve_model(self, provider: Literal["groq", "gemini"] | None = None) -> str:
        chosen = provider or self.llm_provider
        return self.groq_model if chosen == "groq" else self.gemini_model

    def require_api_key(self, provider: Literal["groq", "gemini"] | None = None) -> str:
        chosen = provider or self.llm_provider
        if chosen == "groq":
            if not self.groq_api_key or self.groq_api_key.startswith("gsk_your"):
                raise ValueError(
                    "GROQ_API_KEY no configurada. Copiá .env.example a .env y pegá tu key."
                )
            return self.groq_api_key
        if not self.gemini_api_key or self.gemini_api_key.startswith("your_gemini"):
            raise ValueError(
                "GEMINI_API_KEY no configurada. Copiá .env.example a .env y pegá tu key."
            )
        return self.gemini_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
