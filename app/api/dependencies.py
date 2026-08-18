from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assistant_service import AssistantService
from app.application.memory_service import MemoryService
from app.application.prompt_builder import PromptBuilder
from app.application.readiness_service import ReadinessService
from app.core.config import Settings
from app.domain.ai import AIProvider
from app.domain.memory import MemoryRepository
from app.infrastructure.database import Database
from app.infrastructure.readiness import SqlAlchemyReadinessProbe
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


def get_readiness_service(
    database: Annotated[Database, Depends(get_database)],
) -> ReadinessService:
    return ReadinessService(SqlAlchemyReadinessProbe(database.engine))


ReadinessServiceDep = Annotated[ReadinessService, Depends(get_readiness_service)]


def get_ai_provider(request: Request) -> AIProvider:
    return cast(AIProvider, _app_resource(request, "ai_provider"))


def get_memory_repository(session: SessionDep) -> MemoryRepository:
    return SqlAlchemyMemoryRepository(session)


def get_memory_service(
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
) -> MemoryService:
    return MemoryService(repository)


def get_prompt_builder(request: Request) -> PromptBuilder:
    return cast(PromptBuilder, _app_resource(request, "prompt_builder"))


def get_assistant_service(
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    memory_service: Annotated[MemoryService, Depends(get_memory_service)],
    prompt_builder: Annotated[PromptBuilder, Depends(get_prompt_builder)],
) -> AssistantService:
    return AssistantService(provider, memory_service, prompt_builder)
