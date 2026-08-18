from functools import lru_cache

from pydantic import Field, SecretStr
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

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis"

    ai_provider: str = "gemini"
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-3.6-flash"

    device_admin_token: SecretStr | None = Field(default=None, repr=False)
    device_pairing_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    device_heartbeat_interval_seconds: int = Field(default=30, ge=5, le=300)
    device_command_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    device_max_message_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
