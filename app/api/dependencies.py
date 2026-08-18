from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assistant_service import AssistantService
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings, get_settings
from app.domain.ai import AIProvider
from app.infrastructure.ai.gemini import GeminiProvider
from app.infrastructure.database import get_session
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_ai_provider(settings: SettingsDep) -> AIProvider:
    provider_name = settings.ai_provider.lower()
    if provider_name == "gemini":
        if not settings.gemini_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gemini is selected but JARVIS_GEMINI_API_KEY is not configured.",
            )
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"AI provider '{provider_name}' is not implemented yet.",
    )


def get_memory_repository(session: SessionDep) -> SqlAlchemyMemoryRepository:
    return SqlAlchemyMemoryRepository(session)


def get_prompt_builder(settings: SettingsDep) -> PromptBuilder:
    path = Path(__file__).resolve().parent.parent / "prompts" / "base_system.txt"
    return PromptBuilder(settings, path)


def get_assistant_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    repository: Annotated[SqlAlchemyMemoryRepository, Depends(get_memory_repository)],
    prompt_builder: Annotated[PromptBuilder, Depends(get_prompt_builder)],
) -> AssistantService:
    return AssistantService(provider, repository, prompt_builder)
