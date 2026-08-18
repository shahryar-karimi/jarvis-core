from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class DeviceCapability(StrEnum):
    OPEN_APP = "open_app"
    FILES = "files"
    SYSTEM = "system"


class DeviceStatus(StrEnum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability: DeviceCapability
    actions: frozenset[str]

    def __post_init__(self) -> None:
        normalized = frozenset(action.strip() for action in self.actions)
        if not normalized or any(not action for action in normalized):
            raise ValueError("a capability must define at least one action")
        object.__setattr__(self, "actions", normalized)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Device:
    id: UUID
    name: str
    granted_capabilities: frozenset[DeviceCapability]
    paired_at: datetime
    status: DeviceStatus = DeviceStatus.OFFLINE
    advertised_capabilities: frozenset[DeviceCapability] = field(
        default_factory=frozenset
    )
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        if not normalized_name or len(normalized_name) > 120:
            raise ValueError("device name must contain 1 to 120 characters")
        _require_utc(self.paired_at, "paired_at")
        if self.last_seen_at is not None:
            _require_utc(self.last_seen_at, "last_seen_at")
        if self.revoked_at is not None:
            _require_utc(self.revoked_at, "revoked_at")
        if (self.status is DeviceStatus.REVOKED) != (self.revoked_at is not None):
            raise ValueError("revoked device status and timestamp must agree")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(
            self,
            "granted_capabilities",
            frozenset(self.granted_capabilities),
        )
        object.__setattr__(
            self,
            "advertised_capabilities",
            frozenset(self.advertised_capabilities),
        )

    @property
    def available_capabilities(self) -> frozenset[DeviceCapability]:
        return self.granted_capabilities & self.advertised_capabilities


@dataclass(frozen=True, slots=True)
class DeviceConnectionState:
    session_id: UUID
    status: DeviceStatus
    advertised_capabilities: frozenset[DeviceCapability]
    effective_capabilities: frozenset[DeviceCapability]
    connected_at: datetime
    last_seen_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.connected_at, "connected_at")
        _require_utc(self.last_seen_at, "last_seen_at")
        object.__setattr__(
            self,
            "advertised_capabilities",
            frozenset(self.advertised_capabilities),
        )
        object.__setattr__(
            self,
            "effective_capabilities",
            frozenset(self.effective_capabilities),
        )
        if not self.effective_capabilities <= self.advertised_capabilities:
            raise ValueError(
                "effective capabilities must be advertised by the device"
            )


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    id: UUID
    device_id: UUID
    capability: DeviceCapability
    action: str
    arguments: dict[str, Any]
    issued_at: datetime
    deadline_at: datetime

    def __post_init__(self) -> None:
        action = self.action.strip()
        if not action or len(action) > 80:
            raise ValueError("command action must contain 1 to 80 characters")
        if not action[0].isalpha() or any(
            not (character.isalnum() or character in "_.-")
            for character in action
        ):
            raise ValueError("command action contains unsupported characters")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.deadline_at, "deadline_at")
        if self.deadline_at <= self.issued_at:
            raise ValueError("command deadline must be after its issue time")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    command_id: UUID
    device_id: UUID
    succeeded: bool
    completed_at: datetime
    output: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.completed_at, "completed_at")
        if self.succeeded and self.error is not None:
            raise ValueError("a successful command result cannot contain an error")
        if not self.succeeded and not (self.error and self.error.strip()):
            raise ValueError("a failed command result must contain an error")
        if self.error is not None and len(self.error) > 2_000:
            raise ValueError("command result error is too long")
