from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    TypeAdapter,
    model_validator,
)

from app.domain.devices import DeviceCapability


PROTOCOL_VERSION = 1
DEVICE_SUBPROTOCOL = "jarvis-device.v1"
DEVICE_HELLO_TIMEOUT_SECONDS = 10.0
MESSAGE_REPLAY_WINDOW = 1_024
MAX_CLOSE_REASON_BYTES = 123
HANDSHAKE_REJECTION_STATUS = 403

CLOSE_GOING_AWAY = 1001
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_MESSAGE_TOO_LARGE = 1009
CLOSE_INTERNAL_ERROR = 1011
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_AUTHENTICATION_FAILED = 4403
CLOSE_TIMEOUT = 4408
CLOSE_SESSION_REPLACED = 4409

PROTOCOL_CLOSE_CODES = {
    CLOSE_GOING_AWAY: "application_shutdown",
    CLOSE_UNSUPPORTED_DATA: "text_json_required",
    CLOSE_MESSAGE_TOO_LARGE: "message_too_large",
    CLOSE_INTERNAL_ERROR: "internal_gateway_error",
    CLOSE_PROTOCOL_ERROR: "invalid_protocol_message",
    CLOSE_AUTHENTICATION_FAILED: "credential_revoked_after_upgrade",
    CLOSE_TIMEOUT: "hello_or_heartbeat_timeout",
    CLOSE_SESSION_REPLACED: "connection_replaced",
}

CLIENT_MESSAGE_TYPES = frozenset(
    {
        "device.hello",
        "heartbeat.pong",
        "capabilities.update",
        "command.result",
    }
)
SERVER_MESSAGE_TYPES = frozenset(
    {
        "server.hello",
        "server.ready",
        "heartbeat.ping",
        "capabilities.ack",
        "command.execute",
        "command.result_rejected",
        "protocol.error",
    }
)


class StrictMessage(BaseModel):
    """A closed v1 object: declared fields are the entire wire shape."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


ProtocolVersion: TypeAlias = Annotated[StrictInt, Field(ge=1, le=1)]
PositiveInteger: TypeAlias = Annotated[StrictInt, Field(gt=0)]
PositiveNumber: TypeAlias = Annotated[
    StrictInt | StrictFloat,
    Field(gt=0),
]


class DeviceHelloPayload(StrictMessage):
    agent: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    platform: str = Field(min_length=1, max_length=80)
    capabilities: set[DeviceCapability] = Field(max_length=32)


class HeartbeatPongPayload(StrictMessage):
    nonce: UUID


class CapabilitiesUpdatePayload(StrictMessage):
    capabilities: set[DeviceCapability] = Field(max_length=32)


class CommandResultPayload(StrictMessage):
    command_id: UUID
    succeeded: StrictBool
    output: JsonValue = None
    error: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_outcome(self) -> CommandResultPayload:
        if self.succeeded and self.error is not None:
            raise ValueError("a successful command result cannot contain an error")
        if not self.succeeded and not (self.error and self.error.strip()):
            raise ValueError("a failed command result must contain an error")
        return self


class DeviceHelloMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["device.hello"]
    message_id: UUID
    session_id: UUID
    payload: DeviceHelloPayload


class HeartbeatPongMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["heartbeat.pong"]
    message_id: UUID
    session_id: UUID
    payload: HeartbeatPongPayload


class CapabilitiesUpdateMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["capabilities.update"]
    message_id: UUID
    session_id: UUID
    payload: CapabilitiesUpdatePayload


class CommandResultMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["command.result"]
    message_id: UUID
    session_id: UUID
    payload: CommandResultPayload


DeviceInboundMessage = Annotated[
    DeviceHelloMessage
    | HeartbeatPongMessage
    | CapabilitiesUpdateMessage
    | CommandResultMessage,
    Field(discriminator="type"),
]
device_message_adapter = TypeAdapter(DeviceInboundMessage)


def _parse_wire_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _validate_wire_timestamp(value: str) -> str:
    """Validate an aware ISO 8601 timestamp without rewriting its text form."""

    _parse_wire_timestamp(value)
    return value


WireTimestamp: TypeAlias = Annotated[str, AfterValidator(_validate_wire_timestamp)]


class ServerHelloPayload(StrictMessage):
    device_id: UUID
    heartbeat_interval_seconds: PositiveNumber
    max_message_bytes: PositiveInteger


class ServerReadyPayload(StrictMessage):
    device_id: UUID
    effective_capabilities: list[DeviceCapability]


class HeartbeatPingPayload(StrictMessage):
    nonce: UUID


class CapabilitiesAckPayload(StrictMessage):
    effective_capabilities: list[DeviceCapability]


class CommandExecutePayload(StrictMessage):
    command_id: UUID
    capability: DeviceCapability
    action: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$",
    )
    arguments: dict[str, JsonValue]
    issued_at: WireTimestamp
    deadline_at: WireTimestamp

    @model_validator(mode="after")
    def validate_deadline(self) -> CommandExecutePayload:
        if _parse_wire_timestamp(self.deadline_at) <= _parse_wire_timestamp(
            self.issued_at
        ):
            raise ValueError("command deadline must be after its issue time")
        return self


class CommandResultRejectedPayload(StrictMessage):
    command_id: UUID
    reason: Literal["command_is_not_pending"]


class ProtocolErrorPayload(StrictMessage):
    code: Literal["device_hello_required", "invalid_message"]
    detail: str = Field(min_length=1, max_length=2_000)


class ServerHelloMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["server.hello"]
    message_id: UUID
    session_id: UUID
    payload: ServerHelloPayload


class ServerReadyMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["server.ready"]
    message_id: UUID
    session_id: UUID
    payload: ServerReadyPayload


class HeartbeatPingMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["heartbeat.ping"]
    message_id: UUID
    session_id: UUID
    payload: HeartbeatPingPayload


class CapabilitiesAckMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["capabilities.ack"]
    message_id: UUID
    session_id: UUID
    payload: CapabilitiesAckPayload


class CommandExecuteMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["command.execute"]
    message_id: UUID
    session_id: UUID
    payload: CommandExecutePayload


class CommandResultRejectedMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["command.result_rejected"]
    message_id: UUID
    session_id: UUID
    payload: CommandResultRejectedPayload


class ProtocolErrorMessage(StrictMessage):
    v: ProtocolVersion
    type: Literal["protocol.error"]
    message_id: UUID
    session_id: UUID
    payload: ProtocolErrorPayload


DeviceOutboundMessage = Annotated[
    ServerHelloMessage
    | ServerReadyMessage
    | HeartbeatPingMessage
    | CapabilitiesAckMessage
    | CommandExecuteMessage
    | CommandResultRejectedMessage
    | ProtocolErrorMessage,
    Field(discriminator="type"),
]
server_message_adapter = TypeAdapter(DeviceOutboundMessage)

ServerMessageType: TypeAlias = Literal[
    "server.hello",
    "server.ready",
    "heartbeat.ping",
    "capabilities.ack",
    "command.execute",
    "command.result_rejected",
    "protocol.error",
]


def server_message(
    message_type: ServerMessageType,
    session_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate a JSON-compatible Core-to-Agent v1 envelope."""

    message = server_message_adapter.validate_python(
        {
            "v": PROTOCOL_VERSION,
            "type": message_type,
            "message_id": uuid4(),
            "session_id": session_id,
            "payload": payload,
        }
    )
    return message.model_dump(mode="json")


def serialize_datetime(value: datetime) -> str:
    return value.isoformat()
