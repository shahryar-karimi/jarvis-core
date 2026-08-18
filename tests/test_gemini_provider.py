from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google import genai

from app.domain.ai import AICapability, AIRequest
from app.infrastructure.ai.gemini import GeminiProvider


def test_gemini_provider_requires_an_api_key() -> None:
    with pytest.raises(ValueError, match="API key is not configured"):
        GeminiProvider("", "gemini-test")


def test_gemini_provider_declares_its_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace())
    provider = GeminiProvider("test-key", "gemini-test")

    assert provider.supports(AICapability.TEXT) is True
    assert provider.supports(AICapability.TOOL_CALLING) is True
    assert provider.supports(AICapability.VISION) is True
    assert provider.supports(AICapability.STRUCTURED_OUTPUT) is True
    assert provider.supports(AICapability.REALTIME_AUDIO) is False
    assert provider.supports(AICapability.EMBEDDINGS) is False


@pytest.mark.asyncio
async def test_gemini_provider_maps_requests_and_responses(monkeypatch) -> None:
    generate_content = AsyncMock(return_value=SimpleNamespace(text="answer"))
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
        )
    )
    monkeypatch.setattr(genai, "Client", lambda api_key: client)
    provider = GeminiProvider("test-key", "gemini-test")

    response = await provider.complete(
        AIRequest(user_message="hello", system_prompt="system rules")
    )

    generate_content.assert_awaited_once()
    call = generate_content.await_args
    assert call.kwargs["model"] == "gemini-test"
    assert call.kwargs["contents"] == "hello"
    assert call.kwargs["config"].system_instruction == "system rules"
    assert response.text == "answer"
    assert response.provider == "gemini"
    assert response.model == "gemini-test"


@pytest.mark.asyncio
async def test_gemini_provider_closes_sync_and_async_clients(monkeypatch) -> None:
    async_close = AsyncMock()
    sync_close = Mock()
    client = SimpleNamespace(
        aio=SimpleNamespace(aclose=async_close),
        close=sync_close,
    )
    monkeypatch.setattr(genai, "Client", lambda api_key: client)
    provider = GeminiProvider("test-key", "gemini-test")

    await provider.aclose()

    async_close.assert_awaited_once_with()
    sync_close.assert_called_once_with()
