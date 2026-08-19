from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from app.core.config import (
    ProductionConfigurationError,
    Settings,
    validate_runtime_settings,
)
from app.main import create_app


def production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": (
            "postgresql+asyncpg://jarvis:f3a5f8d7c1e94846a42da7327f5a4b90@postgres:5432/jarvis"
        ),
        "gemini_api_key": "configured-gemini-api-key",
        "owner_token": SecretStr("owner-token-with-at-least-32-bytes"),
        "device_admin_token": SecretStr("admin-token-with-at-least-32-bytes"),
        "device_credential_digest_key": SecretStr(
            "digest-key-with-at-least-32-bytes",
        ),
        "trusted_hosts": ["core.example.com", "localhost"],
        "cors_origins": [],
        "docs_enabled": False,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_secure_production_settings_are_accepted_and_disable_docs() -> None:
    settings = production_settings()

    validate_runtime_settings(settings)
    application = create_app(settings)

    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None
    assert "f3a5f8d7c1e94846a42da7327f5a4b90" not in repr(settings)


@pytest.mark.asyncio
async def test_production_rejects_untrusted_host_headers() -> None:
    application = create_app(production_settings())
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://core.example.com",
    ) as client:
        accepted = await client.get("/api/v1/health")
        rejected = await client.get(
            "/api/v1/health",
            headers={"host": "attacker.example"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 400


@pytest.mark.parametrize(
    ("updates", "expected_problem"),
    [
        ({"owner_token": SecretStr("short")}, "JARVIS_OWNER_TOKEN"),
        ({"owner_token": SecretStr("é" * 32)}, "JARVIS_OWNER_TOKEN"),
        (
            {
                "device_admin_token": SecretStr(
                    "owner-token-with-at-least-32-bytes",
                ),
            },
            "all production credentials",
        ),
        ({"gemini_api_key": "replace-me"}, "JARVIS_GEMINI_API_KEY"),
        (
            {
                "database_url": ("postgresql+asyncpg://jarvis:jarvis@postgres:5432/jarvis"),
            },
            "JARVIS_DATABASE_URL",
        ),
        ({"trusted_hosts": ["*"]}, "JARVIS_TRUSTED_HOSTS"),
        ({"trusted_hosts": ["https://core.example.com"]}, "JARVIS_TRUSTED_HOSTS"),
        ({"cors_origins": ["http://app.example.com"]}, "JARVIS_CORS_ORIGINS"),
        ({"cors_origins": ["https://*.example.com"]}, "JARVIS_CORS_ORIGINS"),
    ],
)
def test_unsafe_production_settings_fail_before_startup(
    updates: dict[str, object],
    expected_problem: str,
) -> None:
    with pytest.raises(ProductionConfigurationError, match=expected_problem):
        create_app(production_settings(**updates))


def test_nonproduction_configuration_remains_developer_friendly() -> None:
    settings = Settings(_env_file=None, environment="development")

    validate_runtime_settings(settings)


def test_environment_name_is_normalized_and_typos_are_rejected() -> None:
    assert production_settings(environment=" PRODUCTION ").environment == "production"

    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prodution")
