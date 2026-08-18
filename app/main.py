from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import repository models before create_schema() so SQLAlchemy metadata is populated.
from app.infrastructure.repositories import memory as _memory_models  # noqa: F401
from app.api.router import api_router
from app.core.config import get_settings
from app.infrastructure.database import close_database, create_schema


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_schema()
    yield
    await close_database()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="JARVIS Core",
        version="0.1.0",
        description="Cloud brain and orchestration layer for the JARVIS hive.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
