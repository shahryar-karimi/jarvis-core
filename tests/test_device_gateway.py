from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.infrastructure.hive import create_in_memory_hive_resources
from app.main import create_app


ADMIN_TOKEN = "test-device-admin-token-with-enough-entropy"
ADMIN_HEADERS = {"authorization": f"Bearer {ADMIN_TOKEN}"}
DEVICE_PROTOCOL = "jarvis-device.v1"


class FakeDatabase:
    def __init__(self) -> None:
        self.session_factory = object()
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


class FakeAIProvider:
    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


@pytest.fixture
def gateway_app(test_settings: Settings) -> FastAPI:
    settings = test_settings.model_copy(
        update={
            "device_admin_token": SecretStr(ADMIN_TOKEN),
            "device_heartbeat_interval_seconds": 300,
            "device_command_timeout_seconds": 2.0,
        }
    )
    return create_app(
        settings,
        database_factory=lambda _: FakeDatabase(),
        ai_provider_factory=lambda _: FakeAIProvider(),
        hive_factory=create_in_memory_hive_resources,
    )


@pytest.fixture
def short_timeout_gateway_app(test_settings: Settings) -> FastAPI:
    # Bypass the production lower bound to exercise timeout behavior quickly.
    settings = test_settings.model_copy(
        update={
            "device_admin_token": SecretStr(ADMIN_TOKEN),
            "device_heartbeat_interval_seconds": 300,
            "device_command_timeout_seconds": 0.05,
        }
    )
    return create_app(
        settings,
        database_factory=lambda _: FakeDatabase(),
        ai_provider_factory=lambda _: FakeAIProvider(),
        hive_factory=create_in_memory_hive_resources,
    )


@pytest.fixture
def fast_heartbeat_gateway_app(test_settings: Settings) -> FastAPI:
    # Bypass the production lower bound to avoid wall-clock sleeps in the test.
    settings = test_settings.model_copy(
        update={
            "device_admin_token": SecretStr(ADMIN_TOKEN),
            "device_heartbeat_interval_seconds": 0.02,
            "device_command_timeout_seconds": 2.0,
        }
    )
    return create_app(
        settings,
        database_factory=lambda _: FakeDatabase(),
        ai_provider_factory=lambda _: FakeAIProvider(),
        hive_factory=create_in_memory_hive_resources,
    )


def pair_device(
    client: TestClient,
    *,
    granted_capabilities: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    pairing_response = client.post(
        "/api/v1/devices/pairings",
        headers=ADMIN_HEADERS,
        json={
            "granted_capabilities": granted_capabilities or ["open_app"],
        },
    )
    assert pairing_response.status_code == 201
    assert pairing_response.headers["cache-control"] == "no-store"
    pairing = pairing_response.json()

    claim_response = client.post(
        "/api/v1/devices/pair",
        json={
            "pairing_id": pairing["pairing_id"],
            "pairing_secret": pairing["pairing_secret"],
            "device_name": "Test workstation",
        },
    )
    assert claim_response.status_code == 201
    assert claim_response.headers["cache-control"] == "no-store"
    claim = claim_response.json()
    return claim["device"], claim["device_token"]


def hello_message(
    session_id: str,
    capabilities: list[str],
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "device.hello",
        "message_id": str(uuid4()),
        "session_id": session_id,
        "payload": {
            "agent": "jarvis-agent-test",
            "version": "1.0.0",
            "platform": "windows",
            "capabilities": capabilities,
        },
    }


def test_gateway_pair_connect_execute_result_and_cleanup(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        device, token = pair_device(
            client,
            granted_capabilities=["open_app", "system"],
        )
        device_id = UUID(device["id"])

        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            assert websocket.accepted_subprotocol == DEVICE_PROTOCOL
            server_hello = websocket.receive_json()
            assert server_hello["v"] == 1
            assert server_hello["type"] == "server.hello"
            assert server_hello["payload"]["device_id"] == str(device_id)
            session_id = server_hello["session_id"]

            websocket.send_json(
                hello_message(
                    session_id,
                    ["open_app", "files"],
                )
            )
            ready = websocket.receive_json()
            assert ready["type"] == "server.ready"
            assert ready["session_id"] == session_id
            assert ready["payload"] == {
                "device_id": str(device_id),
                "effective_capabilities": ["open_app"],
            }

            online_response = client.get(
                f"/api/v1/devices/{device_id}",
                headers=ADMIN_HEADERS,
            )
            assert online_response.status_code == 200
            assert online_response.json()["status"] == "online"
            assert online_response.json()["available_capabilities"] == [
                "open_app"
            ]

            unsupported = client.post(
                f"/api/v1/devices/{device_id}/commands",
                headers=ADMIN_HEADERS,
                json={
                    "capability": "open_app",
                    "action": "shell",
                    "arguments": {"command": "whoami"},
                },
            )
            assert unsupported.status_code == 409
            assert "not registered" in unsupported.json()["detail"]

            with ThreadPoolExecutor(max_workers=1) as executor:
                command_response = executor.submit(
                    lambda: client.post(
                        f"/api/v1/devices/{device_id}/commands",
                        headers=ADMIN_HEADERS,
                        json={
                            "capability": "open_app",
                            "action": "open",
                            "arguments": {"name": "PyCharm"},
                        },
                    )
                )

                command = websocket.receive_json()
                assert command["v"] == 1
                assert command["type"] == "command.execute"
                assert command["session_id"] == session_id
                assert command["payload"]["capability"] == "open_app"
                assert command["payload"]["action"] == "open"
                assert command["payload"]["arguments"] == {"name": "PyCharm"}

                websocket.send_json(
                    {
                        "v": 1,
                        "type": "command.result",
                        "message_id": str(uuid4()),
                        "session_id": session_id,
                        "payload": {
                            "command_id": command["payload"]["command_id"],
                            "succeeded": True,
                            "output": {"opened": "PyCharm"},
                            "error": None,
                        },
                    }
                )
                response = command_response.result(timeout=3)

            assert response.status_code == 200
            body = response.json()
            assert body["id"] == command["payload"]["command_id"]
            assert body["device_id"] == str(device_id)
            assert body["result"] == {
                "succeeded": True,
                "output": {"opened": "PyCharm"},
                "error": None,
                "completed_at": body["result"]["completed_at"],
            }

        offline_response = client.get(
            f"/api/v1/devices/{device_id}",
            headers=ADMIN_HEADERS,
        )
        assert offline_response.status_code == 200
        assert offline_response.json()["status"] == "offline"
        assert offline_response.json()["advertised_capabilities"] == []
        assert offline_response.json()["available_capabilities"] == []


def test_gateway_rejects_missing_and_tampered_authentication(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        with pytest.raises(WebSocketDenialResponse) as missing:
            with client.websocket_connect(
                "/api/v1/devices/ws",
                subprotocols=[DEVICE_PROTOCOL],
            ):
                pytest.fail("gateway accepted a WebSocket without a credential")
        assert missing.value.status_code == 403

        _, token = pair_device(client)
        with pytest.raises(WebSocketDenialResponse) as query_token:
            with client.websocket_connect(
                f"/api/v1/devices/ws?token={token}",
                subprotocols=[DEVICE_PROTOCOL],
            ):
                pytest.fail("gateway accepted a credential from the URL")
        assert query_token.value.status_code == 403

        with pytest.raises(WebSocketDenialResponse) as tampered:
            with client.websocket_connect(
                "/api/v1/devices/ws",
                headers={"authorization": f"Bearer {token}tampered"},
                subprotocols=[DEVICE_PROTOCOL],
            ):
                pytest.fail("gateway accepted a tampered device credential")
        assert tampered.value.status_code == 403
        assert token not in tampered.value.text


def test_gateway_requires_the_versioned_device_subprotocol(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        _, token = pair_device(client)

        with pytest.raises(WebSocketDenialResponse) as rejected:
            with client.websocket_connect(
                "/api/v1/devices/ws",
                headers={"authorization": f"Bearer {token}"},
                subprotocols=["jarvis-device.v999"],
            ):
                pytest.fail("gateway accepted an unsupported subprotocol")

        assert rejected.value.status_code == 403


def test_gateway_requires_device_hello_as_the_first_message(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        _, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            websocket.send_json(
                {
                    "v": 1,
                    "type": "capabilities.update",
                    "message_id": str(uuid4()),
                    "session_id": server_hello["session_id"],
                    "payload": {"capabilities": ["open_app"]},
                }
            )

            assert websocket.receive_json()["type"] == "protocol.error"
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 4400


@pytest.mark.parametrize("violation", ["replay", "wrong_session"])
def test_gateway_rejects_replays_and_wrong_sessions(
    gateway_app: FastAPI,
    violation: str,
) -> None:
    with TestClient(gateway_app) as client:
        _, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            hello = hello_message(server_hello["session_id"], ["open_app"])
            websocket.send_json(hello)
            assert websocket.receive_json()["type"] == "server.ready"

            websocket.send_json(
                {
                    "v": 1,
                    "type": "capabilities.update",
                    "message_id": (
                        hello["message_id"]
                        if violation == "replay"
                        else str(uuid4())
                    ),
                    "session_id": (
                        str(uuid4())
                        if violation == "wrong_session"
                        else server_hello["session_id"]
                    ),
                    "payload": {"capabilities": ["open_app"]},
                }
            )

            assert websocket.receive_json()["type"] == "protocol.error"
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 4400


@pytest.mark.parametrize(
    ("frame", "expected_code"),
    [
        (b"{}", 1003),
        (
            json.dumps(
                {
                    "v": 1,
                    "type": "device.hello",
                    "padding": "x" * 66_000,
                }
            ),
            1009,
        ),
    ],
    ids=["binary", "oversized"],
)
def test_gateway_rejects_binary_and_oversized_frames(
    gateway_app: FastAPI,
    frame: bytes | str,
    expected_code: int,
) -> None:
    with TestClient(gateway_app) as client:
        _, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            websocket.receive_json()
            if isinstance(frame, bytes):
                websocket.send_bytes(frame)
            else:
                websocket.send_text(frame)

            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == expected_code


def test_operator_routes_require_admin_and_revocation_blocks_reconnect(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        unauthenticated = client.post(
            "/api/v1/devices/pairings",
            json={"granted_capabilities": ["open_app"]},
        )
        invalid = client.get(
            "/api/v1/devices/capabilities",
            headers={"authorization": "Bearer wrong-admin-credential"},
        )

        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"
        assert invalid.status_code == 401

        capabilities = client.get(
            "/api/v1/devices/capabilities",
            headers=ADMIN_HEADERS,
        )
        assert capabilities.status_code == 200
        assert capabilities.json() == [
            {"capability": "files", "actions": ["list", "read", "write"]},
            {"capability": "open_app", "actions": ["open"]},
            {
                "capability": "system",
                "actions": ["get_info", "get_status"],
            },
        ]

        device, token = pair_device(client)
        revoked = client.delete(
            f"/api/v1/devices/{device['id']}",
            headers=ADMIN_HEADERS,
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        with pytest.raises(WebSocketDenialResponse) as rejected:
            with client.websocket_connect(
                "/api/v1/devices/ws",
                headers={"authorization": f"Bearer {token}"},
                subprotocols=[DEVICE_PROTOCOL],
            ):
                pytest.fail("gateway accepted a revoked device credential")
        assert rejected.value.status_code == 403


def test_blank_device_name_is_rejected_without_consuming_pairing(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        pairing = client.post(
            "/api/v1/devices/pairings",
            headers=ADMIN_HEADERS,
            json={"granted_capabilities": ["open_app"]},
        ).json()

        blank = client.post(
            "/api/v1/devices/pair",
            json={
                "pairing_id": pairing["pairing_id"],
                "pairing_secret": pairing["pairing_secret"],
                "device_name": "   ",
            },
        )
        valid = client.post(
            "/api/v1/devices/pair",
            json={
                "pairing_id": pairing["pairing_id"],
                "pairing_secret": pairing["pairing_secret"],
                "device_name": "  Workstation  ",
            },
        )

        assert blank.status_code == 422
        assert valid.status_code == 201
        assert valid.json()["device"]["name"] == "Workstation"


def test_late_command_result_does_not_disconnect_device(
    short_timeout_gateway_app: FastAPI,
) -> None:
    with TestClient(short_timeout_gateway_app) as client:
        device, token = pair_device(client)
        device_id = UUID(device["id"])

        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            session_id = server_hello["session_id"]
            websocket.send_json(hello_message(session_id, ["open_app"]))
            assert websocket.receive_json()["type"] == "server.ready"

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    lambda: client.post(
                        f"/api/v1/devices/{device_id}/commands",
                        headers=ADMIN_HEADERS,
                        json={
                            "capability": "open_app",
                            "action": "open",
                            "arguments": {"name": "PyCharm"},
                        },
                    )
                )
                command = websocket.receive_json()
                response = pending.result(timeout=1)

            assert response.status_code == 504
            websocket.send_json(
                {
                    "v": 1,
                    "type": "command.result",
                    "message_id": str(uuid4()),
                    "session_id": session_id,
                    "payload": {
                        "command_id": command["payload"]["command_id"],
                        "succeeded": True,
                        "output": {"opened": "PyCharm"},
                        "error": None,
                    },
                }
            )
            rejected = websocket.receive_json()
            assert rejected["type"] == "command.result_rejected"

            websocket.send_json(
                {
                    "v": 1,
                    "type": "capabilities.update",
                    "message_id": str(uuid4()),
                    "session_id": session_id,
                    "payload": {"capabilities": ["open_app"]},
                }
            )
            assert websocket.receive_json()["type"] == "capabilities.ack"


def test_revoking_an_online_device_closes_its_active_session(
    gateway_app: FastAPI,
) -> None:
    with TestClient(gateway_app) as client:
        device, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            websocket.send_json(
                hello_message(server_hello["session_id"], ["open_app"])
            )
            assert websocket.receive_json()["type"] == "server.ready"

            revoked = client.delete(
                f"/api/v1/devices/{device['id']}",
                headers=ADMIN_HEADERS,
            )

            assert revoked.status_code == 200
            assert revoked.json()["status"] == "revoked"
            with pytest.raises(WebSocketDisconnect) as disconnected:
                websocket.receive_json()
            assert disconnected.value.code == 4403


def test_gateway_heartbeat_updates_presence(
    fast_heartbeat_gateway_app: FastAPI,
) -> None:
    with TestClient(fast_heartbeat_gateway_app) as client:
        device, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            session_id = server_hello["session_id"]
            websocket.send_json(hello_message(session_id, ["open_app"]))
            assert websocket.receive_json()["type"] == "server.ready"
            before = client.get(
                f"/api/v1/devices/{device['id']}",
                headers=ADMIN_HEADERS,
            ).json()["last_seen_at"]

            ping = websocket.receive_json()
            assert ping["type"] == "heartbeat.ping"
            websocket.send_json(
                {
                    "v": 1,
                    "type": "heartbeat.pong",
                    "message_id": str(uuid4()),
                    "session_id": session_id,
                    "payload": {"nonce": ping["payload"]["nonce"]},
                }
            )
            websocket.send_json(
                {
                    "v": 1,
                    "type": "capabilities.update",
                    "message_id": str(uuid4()),
                    "session_id": session_id,
                    "payload": {"capabilities": ["open_app"]},
                }
            )
            assert websocket.receive_json()["type"] == "capabilities.ack"
            after = client.get(
                f"/api/v1/devices/{device['id']}",
                headers=ADMIN_HEADERS,
            ).json()["last_seen_at"]

            assert after > before


@pytest.mark.parametrize(
    ("send_wrong_nonce", "expected_code"),
    [(True, 4400), (False, 4408)],
)
def test_gateway_rejects_bad_or_missing_heartbeat_pongs(
    fast_heartbeat_gateway_app: FastAPI,
    send_wrong_nonce: bool,
    expected_code: int,
) -> None:
    with TestClient(fast_heartbeat_gateway_app) as client:
        _, token = pair_device(client)
        with client.websocket_connect(
            "/api/v1/devices/ws",
            headers={"authorization": f"Bearer {token}"},
            subprotocols=[DEVICE_PROTOCOL],
        ) as websocket:
            server_hello = websocket.receive_json()
            session_id = server_hello["session_id"]
            websocket.send_json(hello_message(session_id, ["open_app"]))
            assert websocket.receive_json()["type"] == "server.ready"
            assert websocket.receive_json()["type"] == "heartbeat.ping"

            if send_wrong_nonce:
                websocket.send_json(
                    {
                        "v": 1,
                        "type": "heartbeat.pong",
                        "message_id": str(uuid4()),
                        "session_id": session_id,
                        "payload": {"nonce": str(uuid4())},
                    }
                )
                assert websocket.receive_json()["type"] == "protocol.error"

            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == expected_code
