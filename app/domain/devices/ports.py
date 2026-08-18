from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.devices.models import (
    CapabilityDefinition,
    Device,
    DeviceCapability,
    DeviceCommand,
    DeviceCommandResult,
    DeviceConnectionState,
)


class CapabilityRegistry(ABC):
    @abstractmethod
    async def list_all(self) -> list[CapabilityDefinition]:
        raise NotImplementedError

    @abstractmethod
    async def allows(self, capability: DeviceCapability, action: str) -> bool:
        raise NotImplementedError


class DeviceRegistry(ABC):
    @abstractmethod
    async def add(self, device: Device) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, device_id: UUID) -> Device | None:
        raise NotImplementedError

    @abstractmethod
    async def list_all(self) -> list[Device]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, device: Device) -> None:
        raise NotImplementedError

    @abstractmethod
    async def remove(self, device_id: UUID) -> bool:
        """Remove a registration for transaction rollback or explicit purge."""

        raise NotImplementedError


class DeviceTransport(ABC):
    @abstractmethod
    async def send_command(
        self,
        command: DeviceCommand,
        session_id: UUID,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self, code: int, reason: str) -> None:
        raise NotImplementedError


class DeviceConnectionManager(ABC):
    @abstractmethod
    async def connect(self, device_id: UUID, transport: DeviceTransport) -> UUID:
        raise NotImplementedError

    @abstractmethod
    async def activate(
        self,
        device_id: UUID,
        session_id: UUID,
        advertised_capabilities: frozenset[DeviceCapability],
        effective_capabilities: frozenset[DeviceCapability],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self, device_id: UUID, session_id: UUID) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def disconnect_device(
        self,
        device_id: UUID,
        code: int,
        reason: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_state(self, device_id: UUID) -> DeviceConnectionState | None:
        raise NotImplementedError

    @abstractmethod
    async def touch(self, device_id: UUID, session_id: UUID) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self,
        command: DeviceCommand,
        timeout_seconds: float,
    ) -> DeviceCommandResult:
        raise NotImplementedError

    @abstractmethod
    async def resolve(
        self,
        device_id: UUID,
        session_id: UUID,
        result: DeviceCommandResult,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def close_all(self) -> None:
        raise NotImplementedError
