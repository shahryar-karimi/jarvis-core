from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.devices import (
    CapabilityDefinition,
    Device,
    DeviceCapability,
    DeviceCommand,
    DeviceCommandResult,
    DeviceStatus,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairingCreateRequest(StrictSchema):
    granted_capabilities: set[DeviceCapability] = Field(
        min_length=1,
        max_length=3,
    )


class PairingResponse(StrictSchema):
    pairing_id: UUID
    pairing_secret: str
    granted_capabilities: list[DeviceCapability]
    expires_at: datetime


class PairDeviceRequest(StrictSchema):
    pairing_id: UUID
    pairing_secret: str = Field(min_length=16, max_length=256)
    device_name: str = Field(min_length=1, max_length=120)

    @field_validator("device_name")
    @classmethod
    def normalize_device_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device name cannot be blank")
        return normalized


class DeviceResponse(StrictSchema):
    id: UUID
    name: str
    status: DeviceStatus
    granted_capabilities: list[DeviceCapability]
    advertised_capabilities: list[DeviceCapability]
    available_capabilities: list[DeviceCapability]
    paired_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, device: Device) -> DeviceResponse:
        return cls(
            id=device.id,
            name=device.name,
            status=device.status,
            granted_capabilities=sorted(device.granted_capabilities),
            advertised_capabilities=sorted(device.advertised_capabilities),
            available_capabilities=sorted(device.available_capabilities),
            paired_at=device.paired_at,
            last_seen_at=device.last_seen_at,
            revoked_at=device.revoked_at,
        )


class PairDeviceResponse(StrictSchema):
    device: DeviceResponse
    device_token: str


class CapabilityResponse(StrictSchema):
    capability: DeviceCapability
    actions: list[str]

    @classmethod
    def from_domain(cls, definition: CapabilityDefinition) -> CapabilityResponse:
        return cls(
            capability=definition.capability,
            actions=sorted(definition.actions),
        )


class DeviceCommandRequest(StrictSchema):
    capability: DeviceCapability
    action: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$",
    )
    arguments: dict[str, Any] = Field(default_factory=dict)


class DeviceCommandResultResponse(StrictSchema):
    succeeded: bool
    output: Any = None
    error: str | None
    completed_at: datetime

    @model_validator(mode="after")
    def validate_outcome(self) -> DeviceCommandResultResponse:
        if self.succeeded and self.error is not None:
            raise ValueError("a successful result cannot contain an error")
        if not self.succeeded and not self.error:
            raise ValueError("a failed result must contain an error")
        return self

    @classmethod
    def from_domain(
        cls,
        result: DeviceCommandResult,
    ) -> DeviceCommandResultResponse:
        return cls(
            succeeded=result.succeeded,
            output=result.output,
            error=result.error,
            completed_at=result.completed_at,
        )


class DeviceCommandResponse(StrictSchema):
    id: UUID
    device_id: UUID
    capability: DeviceCapability
    action: str
    arguments: dict[str, Any]
    issued_at: datetime
    deadline_at: datetime
    result: DeviceCommandResultResponse

    @classmethod
    def from_domain(
        cls,
        command: DeviceCommand,
        result: DeviceCommandResult,
    ) -> DeviceCommandResponse:
        return cls(
            id=command.id,
            device_id=command.device_id,
            capability=command.capability,
            action=command.action,
            arguments=command.arguments,
            issued_at=command.issued_at,
            deadline_at=command.deadline_at,
            result=DeviceCommandResultResponse.from_domain(result),
        )
