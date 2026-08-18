from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exception_handlers import device_error_handler, memory_not_found_handler
from app.api.router import api_router
from app.application.memory_service import MemoryNotFoundError
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings, get_settings
from app.domain.ai import AIProvider
from app.domain.devices import DeviceError
from app.infrastructure.ai.factory import create_ai_provider
from app.infrastructure.database import Database
from app.infrastructure.hive import HiveResources, create_hive_resources


DatabaseFactory = Callable[[str], Database]
AIProviderFactory = Callable[[Settings], AIProvider]
HiveFactory = Callable[[Settings], HiveResources]


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: DatabaseFactory = Database,
    ai_provider_factory: AIProviderFactory = create_ai_provider,
    hive_factory: HiveFactory = create_hive_resources,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    prompt_path = Path(__file__).resolve().parent / "prompts" / "base_system.txt"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = database_factory(resolved_settings.database_url)
        provider: AIProvider | None = None
        hive: HiveResources | None = None
        application.state.database = database

        try:
            provider = ai_provider_factory(resolved_settings)
            application.state.ai_provider = provider
            application.state.prompt_builder = PromptBuilder(
                resolved_settings,
                prompt_path,
            )
            hive = hive_factory(resolved_settings)
            application.state.hive = hive
            yield
        finally:
            try:
                if hive is not None:
                    await hive.aclose()
            finally:
                try:
                    if provider is not None:
                        await provider.aclose()
                finally:
                    try:
                        await database.dispose()
                    finally:
                        for state_name in (
                            "database",
                            "ai_provider",
                            "prompt_builder",
                            "hive",
                        ):
                            if hasattr(application.state, state_name):
                                delattr(application.state, state_name)

    application = FastAPI(
        title="JARVIS Core",
        version="0.1.0",
        description="Cloud brain and orchestration layer for JARVIS.",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.add_exception_handler(
        MemoryNotFoundError,
        memory_not_found_handler,
    )
    application.add_exception_handler(DeviceError, device_error_handler)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    return application


app = create_app()
