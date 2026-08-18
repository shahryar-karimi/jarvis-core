from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.infrastructure.ai import factory
from app.infrastructure.ai.factory import AIProviderConfigurationError


def provider_settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "ai_provider": "gemini",
        "gemini_api_key": "test-key",
        "gemini_model": "gemini-test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_provider_factory_normalizes_name_and_passes_configuration(
    monkeypatch,
) -> None:
    created_with: list[tuple[str, str]] = []

    def fake_gemini(api_key: str, model: str):
        created_with.append((api_key, model))
        return SimpleNamespace(name="gemini")

    monkeypatch.setattr(factory, "GeminiProvider", fake_gemini)

    provider = factory.create_ai_provider(
        provider_settings(ai_provider="  GeMiNi  ")
    )

    assert provider.name == "gemini"
    assert created_with == [("test-key", "gemini-test")]


def test_provider_factory_rejects_missing_credentials() -> None:
    with pytest.raises(
        AIProviderConfigurationError,
        match="JARVIS_GEMINI_API_KEY",
    ):
        factory.create_ai_provider(provider_settings(gemini_api_key=None))


def test_provider_factory_rejects_unsupported_provider() -> None:
    with pytest.raises(AIProviderConfigurationError, match="not implemented"):
        factory.create_ai_provider(provider_settings(ai_provider="unknown"))
