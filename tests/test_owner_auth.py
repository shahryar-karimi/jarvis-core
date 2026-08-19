from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.device_dependencies import get_capability_registry
from app.core.config import Settings
from app.main import create_app

DEVICE_ADMIN_TOKEN = "test-device-admin-token-with-enough-entropy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/chat", {"message": "hello"}),
        ("GET", "/api/v1/memories", None),
        (
            "PUT",
            "/api/v1/memories",
            {"category": "preferences", "key": "editor", "value": "PyCharm"},
        ),
        ("DELETE", "/api/v1/memories/preferences/editor", None),
    ],
)
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic not-a-bearer-token", "Bearer wrong-owner-token"],
)
async def test_owner_routes_reject_missing_or_invalid_credentials(
    app,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    authorization: str | None,
) -> None:
    headers = {"authorization": authorization} if authorization else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            method,
            path,
            headers=headers,
            json=body,
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Invalid owner credential."}
    assert "wrong-owner-token" not in response.text


@pytest.mark.asyncio
async def test_owner_auth_runs_before_request_body_validation(
    app,
    owner_headers: dict[str, str],
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.post("/api/v1/chat", json={})
        authenticated = await client.post(
            "/api/v1/chat",
            headers=owner_headers,
            json={},
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured",
    [
        None,
        SecretStr("too-short"),
        SecretStr("replace-with-a-random-secret"),
        SecretStr("é" * 32),
    ],
)
async def test_unconfigured_owner_auth_fails_closed_but_health_stays_public(
    test_settings: Settings,
    configured: SecretStr | None,
) -> None:
    settings = test_settings.model_copy(update={"owner_token": configured})
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        protected = await client.get(
            "/api/v1/memories",
            headers={"authorization": "Bearer replace-with-a-random-secret"},
        )
        health = await client.get("/api/v1/health")

    assert protected.status_code == 503
    assert protected.json() == {"detail": "Owner API authentication is not configured."}
    assert health.status_code == 200


@pytest.mark.asyncio
async def test_non_ascii_bearer_bytes_are_rejected_without_server_errors(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(
        update={"device_admin_token": SecretStr(DEVICE_ADMIN_TOKEN)}
    )
    application = create_app(settings)
    application.dependency_overrides[get_capability_registry] = lambda: object()
    transport = ASGITransport(app=application)
    malformed = [(b"authorization", b"Bearer \xff\xfe")]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        owner_response = await client.get(
            "/api/v1/memories",
            headers=malformed,
        )
        device_admin_response = await client.get(
            "/api/v1/devices/capabilities",
            headers=malformed,
        )

    assert owner_response.status_code == 401
    assert owner_response.headers["www-authenticate"] == "Bearer"
    assert device_admin_response.status_code == 401
    assert device_admin_response.headers["www-authenticate"] == "Bearer"


def test_owner_and_device_admin_tokens_are_separate_trust_domains(
    test_settings: Settings,
    owner_headers: dict[str, str],
) -> None:
    settings = test_settings.model_copy(
        update={"device_admin_token": SecretStr(DEVICE_ADMIN_TOKEN)}
    )

    class FakeDatabase:
        session_factory = object()

        async def dispose(self) -> None:
            pass

    class FakeAIProvider:
        async def aclose(self) -> None:
            pass

    application = create_app(
        settings,
        database_factory=lambda _: FakeDatabase(),
        ai_provider_factory=lambda _: FakeAIProvider(),
    )
    admin_headers = {"authorization": f"Bearer {DEVICE_ADMIN_TOKEN}"}

    with TestClient(application) as client:
        owner_using_admin = client.get(
            "/api/v1/memories",
            headers=admin_headers,
        )
        admin_using_owner = client.get(
            "/api/v1/devices/capabilities",
            headers=owner_headers,
        )

    assert owner_using_admin.status_code == 401
    assert admin_using_owner.status_code == 401


def test_openapi_distinguishes_owner_and_device_admin_security(app) -> None:
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]

    assert schemes["OwnerBearer"] == {
        "type": "http",
        "description": "Owner credential for chat, memory, and task APIs.",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }
    assert schemes["DeviceAdminBearer"] == {
        "type": "http",
        "description": "Operator credential for device administration APIs.",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }

    owner_operations = [
        ("/api/v1/chat", "post"),
        ("/api/v1/memories", "get"),
        ("/api/v1/memories", "put"),
        ("/api/v1/memories/{category}/{key}", "delete"),
    ]
    for path, method in owner_operations:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"OwnerBearer": []}]
        assert {"401", "503"} <= operation["responses"].keys()

    device_admin_operations = [
        ("/api/v1/devices/pairings", "post"),
        ("/api/v1/devices/capabilities", "get"),
        ("/api/v1/devices", "get"),
        ("/api/v1/devices/{device_id}", "get"),
        ("/api/v1/devices/{device_id}", "delete"),
        ("/api/v1/devices/{device_id}/commands", "post"),
    ]
    for path, method in device_admin_operations:
        assert schema["paths"][path][method]["security"] == [{"DeviceAdminBearer": []}]

    assert "security" not in schema["paths"]["/api/v1/health"]["get"]
    assert "security" not in schema["paths"]["/api/v1/health/ready"]["get"]
    assert "security" not in schema["paths"]["/api/v1/devices/pair"]["post"]


def test_jarvis_owner_token_is_loaded_from_environment(monkeypatch) -> None:
    token = "environment-owner-token-with-enough-entropy"
    monkeypatch.setenv("JARVIS_OWNER_TOKEN", token)

    settings = Settings(_env_file=None)

    assert settings.owner_token is not None
    assert settings.owner_token.get_secret_value() == token


def test_every_http_operation_has_an_explicit_security_policy(app) -> None:
    schema = app.openapi()
    expected_public = {
        ("/api/v1/health", "get"),
        ("/api/v1/health/ready", "get"),
        ("/api/v1/devices/pair", "post"),
    }
    expected_owner = {
        ("/api/v1/chat", "post"),
        ("/api/v1/memories", "get"),
        ("/api/v1/memories", "put"),
        ("/api/v1/memories/{category}/{key}", "delete"),
    }
    expected_device_admin = {
        ("/api/v1/devices/pairings", "post"),
        ("/api/v1/devices/capabilities", "get"),
        ("/api/v1/devices", "get"),
        ("/api/v1/devices/{device_id}", "get"),
        ("/api/v1/devices/{device_id}", "delete"),
        ("/api/v1/devices/{device_id}/commands", "post"),
    }
    expected = expected_public | expected_owner | expected_device_admin
    operations = {
        (path, method)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method in {"get", "put", "post", "delete", "patch"}
    }
    assert operations == expected

    for path, method in expected_public:
        assert "security" not in schema["paths"][path][method]
    for path, method in expected_owner:
        assert schema["paths"][path][method]["security"] == [{"OwnerBearer": []}]
    for path, method in expected_device_admin:
        assert schema["paths"][path][method]["security"] == [{"DeviceAdminBearer": []}]
