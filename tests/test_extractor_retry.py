"""Tests del loop de autocorrección del extractor (LLM mockeado)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field, ValidationError

from scraper_agent.config import Settings
from scraper_agent.extractor import StructuredExtractor


class TinySchema(BaseModel):
    title: str
    price: float = Field(..., ge=0)


class _FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = self._create

    def _create(self, **kwargs: Any) -> Any:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_extractor_self_corrects_after_validation_error() -> None:
    bad = ValidationError.from_exception_data(
        "TinySchema",
        [{"type": "missing", "loc": ("price",), "input": {}, "msg": "Field required"}],
    )
    good = TinySchema(title="Mouse", price=19.99)
    fake = _FakeClient([bad, good])
    settings = Settings(
        groq_api_key="gsk_test_key_not_placeholder",
        llm_provider="groq",
        max_extraction_retries=3,
    )

    with patch(
        "scraper_agent.extractor.get_instructor_client",
        return_value=(fake, "fake-model", "groq"),
    ):
        extractor = StructuredExtractor(settings)
        result = await extractor.extract(
            url="https://example.com/p/1",
            markdown="# Mouse\nPrice: 19.99 USD",
            image_urls=[],
            response_model=TinySchema,
            goal="producto con precio",
            provider="groq",
        )

    assert result.title == "Mouse"
    assert result.price == 19.99
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_extractor_raises_after_max_retries() -> None:
    always_bad = ValidationError.from_exception_data(
        "TinySchema",
        [{"type": "missing", "loc": ("title",), "input": {}, "msg": "Field required"}],
    )
    fake = _FakeClient([always_bad, always_bad, always_bad])
    settings = Settings(
        groq_api_key="gsk_test_key_not_placeholder",
        llm_provider="groq",
        max_extraction_retries=3,
    )

    with patch(
        "scraper_agent.extractor.get_instructor_client",
        return_value=(fake, "fake-model", "groq"),
    ):
        extractor = StructuredExtractor(settings)
        with pytest.raises(RuntimeError, match="autocorrección"):
            await extractor.extract(
                url="https://example.com",
                markdown="sin datos",
                image_urls=[],
                response_model=TinySchema,
                provider="groq",
            )

    assert fake.calls == 3
