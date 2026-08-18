from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.application.assistant_service import AssistantService
from app.application.memory_service import MemoryService
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings
from app.domain.ai import AICapability, AIProvider, AIRequest, AIResponse
from app.domain.memory import MemoryCategory, MemoryEntry, MemoryRepository


class FakeProvider(AIProvider):
    name = "fake"

    def supports(self, capability: AICapability) -> bool:
        return True

    async def complete(self, request: AIRequest) -> AIResponse:
        assert "Favorite Editor: VS Code" in request.system_prompt
        return AIResponse(text=f"received:{request.user_message}", provider=self.name, model="fake-1")


class FakeMemoryRepository(MemoryRepository):
    async def list_all(self) -> list[MemoryEntry]:
        return [
            MemoryEntry(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
                "VS Code",
                datetime.now(timezone.utc),
            )
        ]

    async def upsert(self, category: MemoryCategory, key: str, value: str) -> MemoryEntry:
        raise NotImplementedError

    async def delete(self, category: MemoryCategory, key: str) -> bool:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_assistant_service_is_provider_independent(tmp_path: Path) -> None:
    prompt = tmp_path / "base.txt"
    prompt.write_text("BASE", encoding="utf-8")
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    service = AssistantService(
        provider=FakeProvider(),
        memory_service=MemoryService(FakeMemoryRepository()),
        prompt_builder=PromptBuilder(settings, prompt),
    )

    response = await service.chat("hello")

    assert response.text == "received:hello"
    assert response.provider == "fake"
