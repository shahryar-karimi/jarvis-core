from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from starlette.testclient import WebSocketDenialResponse

from app.core.config import Settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEVICE_ADMIN_TOKEN = "restart-test-device-admin-with-enough-entropy"
ADMIN_HEADERS = {
    "authorization": f"Bearer {DEVICE_ADMIN_TOKEN}",
}


def test_device_registration_authentication_and_revocation_survive_restart(
    tmp_path: Path,
    test_settings: Settings,
    fake_provider,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'restart.db').as_posix()}"
    settings = test_settings.model_copy(
        update={
            "database_url": database_url,
            "device_admin_token": SecretStr(DEVICE_ADMIN_TOKEN),
            "device_heartbeat_interval_seconds": 300,
        }
    )
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    asyncio.run(asyncio.to_thread(command.upgrade, migration_config, "head"))

    first_app = create_app(
        settings,
        ai_provider_factory=lambda _: fake_provider,
    )
    with TestClient(first_app) as client:
        pairing = client.post(
            "/api/v1/devices/pairings",
            headers=ADMIN_HEADERS,
            json={"granted_capabilities": ["open_app"]},
        )
        assert pairing.status_code == 201
        challenge = pairing.json()
        claim = client.post(
            "/api/v1/devices/pair",
            json={
                "pairing_id": challenge["pairing_id"],
                "pairing_secret": challenge["pairing_secret"],
                "device_name": "Restart probe",
            },
        )
        assert claim.status_code == 201
        device = claim.json()["device"]
        token = claim.json()["device_token"]

    second_app = create_app(
        settings,
        ai_provider_factory=lambda _: fake_provider,
    )
    with TestClient(second_app) as client:
        listed = client.get("/api/v1/devices", headers=ADMIN_HEADERS)
        assert listed.status_code == 200
        assert listed.json() == [
            {
                **device,
                "status": "offline",
                "advertised_capabilities": [],
                "available_capabilities": [],
                "last_seen_at": None,
            }
        ]

        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=["jarvis-device.v1"],
        ) as websocket:
            server_hello = websocket.receive_json()
            assert server_hello["type"] == "server.hello"
            assert server_hello["payload"]["device_id"] == device["id"]

        revoked = client.delete(
            f"/api/v1/devices/{device['id']}",
            headers=ADMIN_HEADERS,
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

    third_app = create_app(
        settings,
        ai_provider_factory=lambda _: fake_provider,
    )
    with TestClient(third_app) as client:
        persisted = client.get(
            f"/api/v1/devices/{device['id']}",
            headers=ADMIN_HEADERS,
        )
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "revoked"

        with pytest.raises(WebSocketDenialResponse) as rejected:
            with client.websocket_connect(
                "/api/v1/devices/ws",
                headers={"authorization": f"Bearer {token}"},
                subprotocols=["jarvis-device.v1"],
            ):
                pytest.fail("revoked credential was accepted")
        assert rejected.value.status_code == 403
