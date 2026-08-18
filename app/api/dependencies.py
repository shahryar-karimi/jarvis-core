from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assistant_service import AssistantService
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings
from app.domain.ai import AIProvider
from app.infrastructure.database import Database
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository


def _app_resource(request: Request, name: str) -> object:
    try:
        return getattr(request.app.state, name)
    except AttributeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application resources are not initialized.",
        ) from error


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return cast(Database, _app_resource(request, "database"))


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session_factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_ai_provider(request: Request) -> AIProvider:
    return cast(AIProvider, _app_resource(request, "ai_provider"))


def get_memory_repository(session: SessionDep) -> SqlAlchemyMemoryRepository:
    return SqlAlchemyMemoryRepository(session)


def get_prompt_builder(request: Request) -> PromptBuilder:
    return cast(PromptBuilder, _app_resource(request, "prompt_builder"))


def get_assistant_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    repository: Annotated[SqlAlchemyMemoryRepository, Depends(get_memory_repository)],
    prompt_builder: Annotated[PromptBuilder, Depends(get_prompt_builder)],
) -> AssistantService:
    return AssistantService(provider, repository, prompt_builder)
