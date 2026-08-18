from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

import app.api.websocket.protocol as protocol
from app.api.websocket.protocol import (
    CLIENT_MESSAGE_TYPES,
    CLOSE_SESSION_REPLACED,
    DEVICE_HELLO_TIMEOUT_SECONDS,
    DEVICE_SUBPROTOCOL,
    MAX_CLOSE_REASON_BYTES,
    MESSAGE_REPLAY_WINDOW,
    PROTOCOL_CLOSE_CODES,
    PROTOCOL_VERSION,
    SERVER_MESSAGE_TYPES,
    CapabilitiesAckMessage,
    CapabilitiesUpdateMessage,
    CommandExecuteMessage,
    CommandResultMessage,
    CommandResultRejectedMessage,
    DeviceHelloMessage,
    HeartbeatPingMessage,
    HeartbeatPongMessage,
    ProtocolErrorMessage,
    ServerHelloMessage,
    ServerReadyMessage,
    device_message_adapter,
    server_message,
    server_message_adapter,
)
from app.infrastructure.devices import (
    InMemoryCapabilityRegistry,
    InMemoryDeviceConnectionManager,
)
from app.api.websocket.device_gateway import _truncate_close_reason


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "protocols"
    / "jarvis-device-v1.contract.json"
)
DOCUMENT_PATH = (
    PROJECT_ROOT / "docs" / "protocols" / "jarvis-device-v1.md"
)

MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    "device.hello": DeviceHelloMessage,
    "heartbeat.pong": HeartbeatPongMessage,
    "capabilities.update": CapabilitiesUpdateMessage,
    "command.result": CommandResultMessage,
    "server.hello": ServerHelloMessage,
    "server.ready": ServerReadyMessage,
    "heartbeat.ping": HeartbeatPingMessage,
    "capabilities.ack": CapabilitiesAckMessage,
    "command.execute": CommandExecuteMessage,
    "command.result_rejected": CommandResultRejectedMessage,
    "protocol.error": ProtocolErrorMessage,
}


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def normalize_capability_order(message: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(message)
    payload = normalized["payload"]
    for field_name in ("capabilities", "effective_capabilities"):
        if field_name in payload:
            payload[field_name] = sorted(payload[field_name])
    return normalized


@pytest.mark.asyncio
async def test_committed_contract_freezes_protocol_constants_and_actions() -> None:
    contract = load_contract()

    assert contract["contract"] == DEVICE_SUBPROTOCOL
    assert contract["default_endpoint"] == "/api/v1/devices/ws"
    assert contract["subprotocol"] == DEVICE_SUBPROTOCOL
    assert contract["protocol_version"] == PROTOCOL_VERSION
    assert contract["authentication"] == {
        "scheme": "Bearer",
        "location": "Authorization header",
        "query_credentials_accepted": False,
    }
    assert contract["handshake_rejection"] == {
        "http_status": protocol.HANDSHAKE_REJECTION_STATUS,
        "conditions": [
            "gateway_unavailable",
            "subprotocol_not_offered",
            "credential_missing_invalid_or_revoked",
        ],
    }
    assert contract["compatibility"] == {
        "unknown_envelope_fields": "forbidden",
        "unknown_payload_fields": "forbidden",
        "json_numbers": "finite_only",
        "breaking_changes_require": "jarvis-device.v2",
    }
    assert contract["limits"] == {
        "device_hello_timeout_seconds": DEVICE_HELLO_TIMEOUT_SECONDS,
        "message_id_replay_window": MESSAGE_REPLAY_WINDOW,
        "max_close_reason_bytes": MAX_CLOSE_REASON_BYTES,
    }
    assert {
        int(code): meaning
        for code, meaning in contract["close_codes"].items()
    } == PROTOCOL_CLOSE_CODES
    assert (
        InMemoryDeviceConnectionManager.REPLACED_CLOSE_CODE
        == CLOSE_SESSION_REPLACED
    )

    definitions = await InMemoryCapabilityRegistry().list_all()
    runtime_actions = {
        definition.capability.value: sorted(definition.actions)
        for definition in definitions
    }
    assert runtime_actions == contract["capability_actions"]


def test_committed_contract_freezes_every_closed_message_shape() -> None:
    contract = load_contract()
    shapes = contract["message_shapes"]
    envelope_fields = contract["envelope_fields"]

    assert set(shapes) == CLIENT_MESSAGE_TYPES | SERVER_MESSAGE_TYPES
    assert set(MESSAGE_MODELS) == set(shapes)

    for message_type, shape in shapes.items():
        model = MESSAGE_MODELS[message_type]
        assert list(model.model_fields) == envelope_fields
        assert all(field.is_required() for field in model.model_fields.values())

        payload_model = model.model_fields["payload"].annotation
        assert isinstance(payload_model, type)
        assert issubclass(payload_model, BaseModel)
        required = [
            name
            for name, field in payload_model.model_fields.items()
            if field.is_required()
        ]
        optional = [
            name
            for name, field in payload_model.model_fields.items()
            if not field.is_required()
        ]
        assert required == shape["required_payload_fields"]
        assert optional == shape["optional_payload_fields"]

        expected_direction = (
            "agent_to_core"
            if message_type in CLIENT_MESSAGE_TYPES
            else "core_to_agent"
        )
        assert shape["direction"] == expected_direction


def test_all_golden_messages_validate_and_round_trip() -> None:
    golden = load_contract()["golden_messages"]

    validated_client_types = set()
    for message in golden["agent_to_core"]:
        validated = device_message_adapter.validate_python(message)
        validated_client_types.add(validated.type)
        assert normalize_capability_order(validated.model_dump(mode="json")) == (
            normalize_capability_order(message)
        )

    validated_server_types = set()
    for message in golden["core_to_agent"]:
        validated = server_message_adapter.validate_python(message)
        validated_server_types.add(validated.type)
        assert normalize_capability_order(validated.model_dump(mode="json")) == (
            normalize_capability_order(message)
        )

    assert validated_client_types == CLIENT_MESSAGE_TYPES
    assert validated_server_types == SERVER_MESSAGE_TYPES


@pytest.mark.parametrize(
    ("direction", "adapter"),
    (
        ("agent_to_core", device_message_adapter),
        ("core_to_agent", server_message_adapter),
    ),
)
def test_v1_rejects_unknown_fields_and_versions(direction, adapter) -> None:
    examples = load_contract()["golden_messages"][direction]

    for example in examples:
        extra_envelope = deepcopy(example)
        extra_envelope["future_extension"] = True
        with pytest.raises(ValidationError):
            adapter.validate_python(extra_envelope)

        extra_payload = deepcopy(example)
        extra_payload["payload"]["future_extension"] = True
        with pytest.raises(ValidationError):
            adapter.validate_python(extra_payload)

        wrong_version = deepcopy(example)
        wrong_version["v"] = 2
        with pytest.raises(ValidationError):
            adapter.validate_python(wrong_version)


def test_server_message_builder_validates_and_preserves_the_wire_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = load_contract()["golden_messages"]["core_to_agent"][0]
    monkeypatch.setattr(protocol, "uuid4", lambda: UUID(golden["message_id"]))

    built = server_message(
        "server.hello",
        UUID(golden["session_id"]),
        golden["payload"],
    )

    assert built == golden

    invalid_payload = dict(golden["payload"])
    invalid_payload["future_extension"] = True
    with pytest.raises(ValidationError):
        server_message(
            "server.hello",
            UUID(golden["session_id"]),
            invalid_payload,
        )


def test_command_timestamps_must_be_timezone_aware_iso_8601() -> None:
    command = deepcopy(
        next(
            message
            for message in load_contract()["golden_messages"]["core_to_agent"]
            if message["type"] == "command.execute"
        )
    )
    command["payload"]["issued_at"] = "2026-08-18T12:00:00"

    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(command)


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_protocol_version_is_the_exact_json_integer(
    invalid_version: object,
) -> None:
    contract = load_contract()["golden_messages"]
    examples = contract["agent_to_core"] + contract["core_to_agent"]

    for example in examples:
        invalid = deepcopy(example)
        invalid["v"] = invalid_version
        adapter = (
            device_message_adapter
            if invalid["type"] in CLIENT_MESSAGE_TYPES
            else server_message_adapter
        )
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


@pytest.mark.parametrize("invalid_boolean", ["yes", "false", 1, 0])
def test_command_result_boolean_is_not_coerced(invalid_boolean: object) -> None:
    result = deepcopy(
        next(
            message
            for message in load_contract()["golden_messages"]["agent_to_core"]
            if message["type"] == "command.result"
        )
    )
    result["payload"]["succeeded"] = invalid_boolean

    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(result)


def test_frozen_cross_field_invariants_are_validated() -> None:
    contract = load_contract()["golden_messages"]
    result = deepcopy(
        next(
            message
            for message in contract["agent_to_core"]
            if message["type"] == "command.result"
        )
    )
    result["payload"]["error"] = "unexpected failure"
    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(result)

    result["payload"].update(succeeded=False, error="   ")
    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(result)

    command = deepcopy(
        next(
            message
            for message in contract["core_to_agent"]
            if message["type"] == "command.execute"
        )
    )
    command["payload"]["deadline_at"] = command["payload"]["issued_at"]
    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(command)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("heartbeat_interval_seconds", True),
        ("heartbeat_interval_seconds", "30"),
        ("max_message_bytes", 65_536.0),
        ("max_message_bytes", True),
    ],
)
def test_server_hello_numbers_are_not_coerced(
    field: str,
    invalid_value: object,
) -> None:
    hello = deepcopy(
        next(
            message
            for message in load_contract()["golden_messages"]["core_to_agent"]
            if message["type"] == "server.hello"
        )
    )
    hello["payload"][field] = invalid_value

    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(hello)


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("agent", "a" * 80, "a" * 81),
        ("version", "v" * 40, "v" * 41),
        ("platform", "p" * 80, "p" * 81),
    ],
)
def test_device_hello_string_limits_are_frozen(
    field: str,
    accepted: str,
    rejected: str,
) -> None:
    hello = deepcopy(
        next(
            message
            for message in load_contract()["golden_messages"]["agent_to_core"]
            if message["type"] == "device.hello"
        )
    )
    hello["payload"][field] = accepted
    device_message_adapter.validate_python(hello)

    hello["payload"][field] = rejected
    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(hello)


def test_command_and_error_limits_are_frozen() -> None:
    contract = load_contract()["golden_messages"]
    result = deepcopy(
        next(
            message
            for message in contract["agent_to_core"]
            if message["type"] == "command.result"
        )
    )
    result["payload"].update(succeeded=False, error="e" * 2_000)
    device_message_adapter.validate_python(result)
    result["payload"]["error"] = "e" * 2_001
    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(result)

    command = deepcopy(
        next(
            message
            for message in contract["core_to_agent"]
            if message["type"] == "command.execute"
        )
    )
    command["payload"]["action"] = "a" * 80
    server_message_adapter.validate_python(command)
    for invalid_action in ("a" * 81, "bad action"):
        command["payload"]["action"] = invalid_action
        with pytest.raises(ValidationError):
            server_message_adapter.validate_python(command)

    protocol_error = deepcopy(
        next(
            message
            for message in contract["core_to_agent"]
            if message["type"] == "protocol.error"
        )
    )
    protocol_error["payload"]["detail"] = "d" * 2_000
    server_message_adapter.validate_python(protocol_error)
    protocol_error["payload"]["detail"] = "d" * 2_001
    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(protocol_error)


def test_protocol_payload_values_remain_json_serializable() -> None:
    contract = load_contract()["golden_messages"]
    result = deepcopy(
        next(
            message
            for message in contract["agent_to_core"]
            if message["type"] == "command.result"
        )
    )
    result["payload"]["output"] = object()
    with pytest.raises(ValidationError):
        device_message_adapter.validate_python(result)

    command = deepcopy(
        next(
            message
            for message in contract["core_to_agent"]
            if message["type"] == "command.execute"
        )
    )
    command["payload"]["arguments"] = {"unsafe": object()}
    with pytest.raises(ValidationError):
        server_message_adapter.validate_python(command)

    for non_finite in (float("nan"), float("inf"), float("-inf")):
        result["payload"]["output"] = non_finite
        with pytest.raises(ValidationError):
            device_message_adapter.validate_python(result)

        command["payload"]["arguments"] = {"non_finite": non_finite}
        with pytest.raises(ValidationError):
            server_message_adapter.validate_python(command)


def test_normative_document_covers_every_frozen_identifier_and_blocker() -> None:
    document = DOCUMENT_PATH.read_text(encoding="utf-8")
    contract = load_contract()

    for message_type in contract["message_shapes"]:
        assert f"`{message_type}`" in document
    for close_code in contract["close_codes"]:
        assert f"`{close_code}`" in document
    for capability, actions in contract["capability_actions"].items():
        assert f"`{capability}`" in document
        for action in actions:
            assert f"`{action}`" in document

    assert "Remaining Agent V1 blocker: action payload policies" in document
    assert "MUST NOT concatenate protocol values into a shell" in document


def test_close_reasons_are_limited_by_utf8_bytes() -> None:
    reason = _truncate_close_reason("é" * MAX_CLOSE_REASON_BYTES)

    assert len(reason.encode("utf-8")) <= MAX_CLOSE_REASON_BYTES
    assert ("é" * MAX_CLOSE_REASON_BYTES).startswith(reason)
