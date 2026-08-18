from functools import lru_cache
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_PLACEHOLDER_SECRETS = {
    "change-me",
    "replace-me",
    "replace-with-a-random-secret",
}


class ProductionConfigurationError(RuntimeError):
    """Raised when production would start with an unsafe configuration."""


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="JARVIS_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = (
        "development"
    )
    api_prefix: str = "/api/v1"
    assistant_name: str = "JARVIS"
    user_name: str = ""
    docs_enabled: bool = True
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])

    database_url: str = Field(
        default="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis",
        repr=False,
    )

    ai_provider: str = "gemini"
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-3.6-flash"

    owner_token: SecretStr | None = Field(default=None, repr=False)
    device_admin_token: SecretStr | None = Field(default=None, repr=False)
    device_credential_digest_key: SecretStr | None = Field(
        default=None,
        repr=False,
    )
    device_pairing_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    device_heartbeat_interval_seconds: int = Field(default=30, ge=5, le=300)
    device_command_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    device_max_message_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
    )

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        return value.strip().casefold() if isinstance(value, str) else value

    @field_validator("trusted_hosts", "cors_origins")
    @classmethod
    def normalize_network_lists(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values]


def validate_runtime_settings(settings: Settings) -> None:
    """Fail fast on unsafe values when Core is configured for production."""

    if settings.environment.strip().casefold() != "production":
        return

    problems: list[str] = []
    owner_token = _secret_value(settings.owner_token)
    admin_token = _secret_value(settings.device_admin_token)
    digest_key = _secret_value(settings.device_credential_digest_key)

    for variable_name, value in (
        ("JARVIS_OWNER_TOKEN", owner_token),
        ("JARVIS_DEVICE_ADMIN_TOKEN", admin_token),
    ):
        if not _is_strong_bearer_secret(value):
            problems.append(
                f"{variable_name} must contain at least 32 ASCII characters"
            )

    if not _is_strong_secret(digest_key):
        problems.append(
            "JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY must contain at least 32 bytes"
        )

    provider_name = settings.ai_provider.strip().casefold()
    gemini_api_key = (settings.gemini_api_key or "").strip()
    if provider_name == "gemini" and not _is_configured_value(gemini_api_key):
        problems.append("JARVIS_GEMINI_API_KEY must be configured for Gemini")

    try:
        database_url = urlsplit(settings.database_url)
        database_password = unquote(database_url.password or "")
    except ValueError:
        database_url = None
        database_password = ""
    if (
        database_url is None
        or database_url.scheme != "postgresql+asyncpg"
        or not database_url.username
        or not database_url.hostname
        or not database_url.path.strip("/")
        or not _is_strong_secret(database_password)
    ):
        problems.append(
            "JARVIS_DATABASE_URL must use PostgreSQL/asyncpg with non-default credentials"
        )

    configured_credentials = (
        owner_token,
        admin_token,
        digest_key,
        database_password,
        gemini_api_key,
    )
    if all(configured_credentials) and len(set(configured_credentials)) != len(
        configured_credentials
    ):
        problems.append("all production credentials and secret keys must differ")

    if not settings.trusted_hosts or any(
        not host.strip()
        or host.strip() == "*"
        or "://" in host
        or "/" in host
        for host in settings.trusted_hosts
    ):
        problems.append("JARVIS_TRUSTED_HOSTS must explicitly list allowed hosts")

    if any(not _is_secure_exact_origin(origin) for origin in settings.cors_origins):
        problems.append(
            "JARVIS_CORS_ORIGINS must contain only exact HTTPS origins"
        )

    if problems:
        joined = "; ".join(problems)
        raise ProductionConfigurationError(
            f"Unsafe production configuration: {joined}."
        )


def _secret_value(secret: SecretStr | None) -> str:
    return secret.get_secret_value().strip() if secret else ""


def _is_strong_secret(value: str) -> bool:
    return len(value.encode("utf-8")) >= 32 and _is_configured_value(value)


def _is_strong_bearer_secret(value: str) -> bool:
    return len(value) >= 32 and value.isascii() and _is_configured_value(value)


def _is_configured_value(value: str) -> bool:
    return bool(value) and value.casefold() not in _PLACEHOLDER_SECRETS


def _is_secure_exact_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and "*" not in (parsed.hostname or "")
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
