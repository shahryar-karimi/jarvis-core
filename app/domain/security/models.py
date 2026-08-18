from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.devices import Device, DeviceCapability


@dataclass(frozen=True, slots=True)
class DevicePairingChallenge:
    id: UUID
    secret: str
    granted_capabilities: frozenset[DeviceCapability]
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.secret) < 16:
            raise ValueError("pairing secret is too short")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("pairing expiry must be timezone-aware")
        object.__setattr__(
            self,
            "granted_capabilities",
            frozenset(self.granted_capabilities),
        )

    def __repr__(self) -> str:
        return (
            "DevicePairingChallenge("
            f"id={self.id!r}, secret='<redacted>', "
            f"granted_capabilities={self.granted_capabilities!r}, "
            f"expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True, slots=True)
class DeviceCredential:
    device: Device
    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("device credential token cannot be empty")

    def __repr__(self) -> str:
        return f"DeviceCredential(device={self.device!r}, token='<redacted>')"
