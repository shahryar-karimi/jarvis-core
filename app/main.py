from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings, get_settings
from app.domain.ai import AIProvider
from app.infrastructure.ai.factory import create_ai_provider
from app.infrastructure.database import Database


DatabaseFactory = Callable[[str], Database]
AIProviderFactory = Callable[[Settings], AIProvider]


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: DatabaseFactory = Database,
    ai_provider_factory: AIProviderFactory = create_ai_provider,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    prompt_path = Path(__file__).resolve().parent / "prompts" / "base_system.txt"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = database_factory(resolved_settings.database_url)
        provider: AIProvider | None = None
        application.state.database = database

        try:
            provider = ai_provider_factory(resolved_settings)
            application.state.ai_provider = provider
            application.state.prompt_builder = PromptBuilder(
                resolved_settings,
                prompt_path,
            )
            yield
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
