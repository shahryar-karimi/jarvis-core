from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.devices import DeviceCapability


PROTOCOL_VERSION = 1
DEVICE_SUBPROTOCOL = "jarvis-device.v1"


class StrictMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    succeeded: bool
    output: Any = None
    error: str | None = Field(default=None, max_length=2_000)


class DeviceHelloMessage(StrictMessage):
    v: Literal[1]
    type: Literal["device.hello"]
    message_id: UUID
    session_id: UUID
    payload: DeviceHelloPayload


class HeartbeatPongMessage(StrictMessage):
    v: Literal[1]
    type: Literal["heartbeat.pong"]
    message_id: UUID
    session_id: UUID
    payload: HeartbeatPongPayload


class CapabilitiesUpdateMessage(StrictMessage):
    v: Literal[1]
    type: Literal["capabilities.update"]
    message_id: UUID
    session_id: UUID
    payload: CapabilitiesUpdatePayload


class CommandResultMessage(StrictMessage):
    v: Literal[1]
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


def server_message(
    message_type: str,
    session_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": message_type,
        "message_id": str(uuid4()),
        "session_id": str(session_id),
        "payload": payload,
    }


def serialize_datetime(value: datetime) -> str:
    return value.isoformat()
