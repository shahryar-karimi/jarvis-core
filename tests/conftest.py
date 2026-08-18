from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.api.dependencies import (
    get_ai_provider,
    get_memory_service,
    get_prompt_builder,
)
from app.application.memory_service import MemoryService
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings
from app.domain.ai import AICapability, AIProvider, AIRequest, AIResponse
from app.domain.memory import MemoryCategory, MemoryEntry, MemoryRepository
from app.main import create_app


class ExternalTestSettings(BaseSettings):
    """External-test switches loaded from the project .env file."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    run_db_tests: bool = False
    run_live_ai_tests: bool = False


class FakeAIProvider(AIProvider):
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[AIRequest] = []

    def supports(self, capability: AICapability) -> bool:
        return capability in {AICapability.TEXT, AICapability.TOOL_CALLING}

    async def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        return AIResponse(
            text=f"JARVIS:{request.user_message}",
            provider=self.name,
            model="fake-1",
        )


class FakeMemoryRepository(MemoryRepository):
    def __init__(self) -> None:
        self._entries: dict[tuple[MemoryCategory, str], MemoryEntry] = {}

    async def list_all(self) -> list[MemoryEntry]:
        return sorted(
            self._entries.values(),
            key=lambda entry: entry.updated_at,
            reverse=True,
        )

    async def upsert(
        self,
        category: MemoryCategory,
        key: str,
        value: str,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            category=category,
            key=key,
            value=value,
            updated_at=datetime.now(timezone.utc),
        )
        self._entries[(category, key)] = entry
        return entry

    async def delete(self, category: MemoryCategory, key: str) -> bool:
        return self._entries.pop((category, key), None) is not None


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        api_prefix="/api/v1",
        assistant_name="JARVIS",
        user_name="Test User",
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        ai_provider="gemini",
        gemini_api_key=None,
    )


@pytest.fixture(scope="session")
def external_test_settings() -> ExternalTestSettings:
    return ExternalTestSettings()


@pytest.fixture
def fake_provider() -> FakeAIProvider:
    return FakeAIProvider()


@pytest.fixture
def fake_memory_repository() -> FakeMemoryRepository:
    return FakeMemoryRepository()


@pytest.fixture
def memory_service(
    fake_memory_repository: FakeMemoryRepository,
) -> MemoryService:
    return MemoryService(fake_memory_repository)


@pytest.fixture
def app(
    tmp_path: Path,
    test_settings: Settings,
    fake_provider: FakeAIProvider,
    memory_service: MemoryService,
) -> Iterator[FastAPI]:
    base_prompt = tmp_path / "base_system.txt"
    base_prompt.write_text(
        "Never claim an external action succeeded unless a tool result confirms it.",
        encoding="utf-8",
    )
    prompt_builder = PromptBuilder(test_settings, base_prompt)

    application = create_app(test_settings)
    application.dependency_overrides[get_ai_provider] = lambda: fake_provider
    application.dependency_overrides[get_memory_service] = lambda: memory_service
    application.dependency_overrides[get_prompt_builder] = lambda: prompt_builder

    yield application
    application.dependency_overrides.clear()
