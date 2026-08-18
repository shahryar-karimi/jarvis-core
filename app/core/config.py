from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="JARVIS_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    api_prefix: str = "/api/v1"
    assistant_name: str = "JARVIS"
    user_name: str = ""

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"

    ai_provider: str = "gemini"
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
