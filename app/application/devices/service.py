from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.domain.devices import (
    CapabilityRegistry,
    CommandResultRejectedError,
    Device,
    DeviceActionUnavailableError,
    DeviceAuthenticationError,
    DeviceCapability,
    DeviceCapabilityUnavailableError,
    DeviceCommand,
    DeviceCommandResult,
    DeviceConnectionManager,
    DeviceNotFoundError,
    DeviceOfflineError,
    DeviceRegistry,
    DeviceRevokedError,
    DeviceStatus,
    DeviceTransport,
    InvalidPairingError,
)
from app.domain.security import (
    DeviceCredential,
    DeviceIdentity,
    DevicePairing,
    DevicePairingChallenge,
)


class DeviceService:
    """Pairing, connection, and command use cases for device agents."""

    def __init__(
        self,
        registry: DeviceRegistry,
        identity: DeviceIdentity,
        pairing: DevicePairing,
        connections: DeviceConnectionManager,
        capability_registry: CapabilityRegistry,
        *,
        pairing_ttl_seconds: int = 300,
        command_timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._registry = registry
        self._identity = identity
        self._pairing = pairing
        self._connections = connections
        self._capability_registry = capability_registry
        self._pairing_ttl = timedelta(seconds=pairing_ttl_seconds)
        self._command_timeout_seconds = command_timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._lifecycle_lock = asyncio.Lock()

    async def create_pairing(
        self,
        granted_capabilities: frozenset[DeviceCapability],
    ) -> DevicePairingChallenge:
        return await self._pairing.create(
            frozenset(granted_capabilities),
            self._pairing_ttl,
        )

    async def pair_device(
        self,
        pairing_id: UUID,
        secret: str,
        device_name: str,
    ) -> DeviceCredential:
        credential = await self._pairing.claim(
            pairing_id,
            secret,
            device_name,
        )
        if credential is None:
            raise InvalidPairingError()
        return credential

    async def authenticate(self, token: str) -> Device:
        device_id = await self._identity.authenticate(token)
        if device_id is None:
            raise DeviceAuthenticationError()
        device = await self._registry.get(device_id)
        if device is None:
            raise DeviceAuthenticationError()
        if device.status is DeviceStatus.REVOKED:
            raise DeviceAuthenticationError()
        return await self._with_runtime_state(device)

    async def list_devices(self) -> list[Device]:
        devices = await self._registry.list_all()
        hydrated = [await self._with_runtime_state(device) for device in devices]
        return sorted(hydrated, key=lambda device: (device.name.casefold(), str(device.id)))

    async def get_device(self, device_id: UUID) -> Device:
        device = await self._registry.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return await self._with_runtime_state(device)

    async def revoke_device(self, device_id: UUID) -> Device:
        async with self._lifecycle_lock:
            device = await self._registry.get(device_id)
            if device is None:
                raise DeviceNotFoundError(device_id)
            if device.status is not DeviceStatus.REVOKED:
                await self._identity.revoke(device_id)
                device = replace(
                    device,
                    status=DeviceStatus.REVOKED,
                    revoked_at=self._clock(),
                    advertised_capabilities=frozenset(),
                )
                await self._registry.save(device)
            await self._connections.disconnect_device(
                device_id,
                4403,
                "Device credential revoked",
            )
            return device

    async def connect_device(
        self,
        device_id: UUID,
        transport: DeviceTransport,
    ) -> UUID:
        async with self._lifecycle_lock:
            device = await self._registry.get(device_id)
            if device is None:
                raise DeviceNotFoundError(device_id)
            if device.status is DeviceStatus.REVOKED:
                raise DeviceRevokedError(device_id)
            return await self._connections.connect(device_id, transport)

    async def activate_device(
        self,
        device_id: UUID,
        session_id: UUID,
        advertised_capabilities: frozenset[DeviceCapability],
    ) -> frozenset[DeviceCapability]:
        device = await self._registry.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        if device.status is DeviceStatus.REVOKED:
            raise DeviceRevokedError(device_id)
        advertised = frozenset(advertised_capabilities)
        effective = advertised & device.granted_capabilities
        await self._connections.activate(
            device_id,
            session_id,
            advertised,
            effective,
        )
        return effective

    async def disconnect_device_session(
        self,
        device_id: UUID,
        session_id: UUID,
    ) -> bool:
        return await self._connections.disconnect(device_id, session_id)

    async def mark_seen(self, device_id: UUID, session_id: UUID) -> bool:
        return await self._connections.touch(device_id, session_id)

    async def execute_command(
        self,
        device_id: UUID,
        capability: DeviceCapability,
        action: str,
        arguments: dict[str, Any],
    ) -> tuple[DeviceCommand, DeviceCommandResult]:
        device = await self.get_device(device_id)
        if device.status is not DeviceStatus.ONLINE:
            raise DeviceOfflineError(device_id)
        if capability not in device.available_capabilities:
            raise DeviceCapabilityUnavailableError(device_id, capability)
        if not await self._capability_registry.allows(capability, action):
            raise DeviceActionUnavailableError(capability, action)

        issued_at = self._clock()
        command = DeviceCommand(
            id=self._id_factory(),
            device_id=device_id,
            capability=capability,
            action=action,
            arguments=arguments,
            issued_at=issued_at,
            deadline_at=issued_at + timedelta(seconds=self._command_timeout_seconds),
        )
        result = await self._connections.execute(
            command,
            self._command_timeout_seconds,
        )
        return command, result

    async def accept_command_result(
        self,
        device_id: UUID,
        session_id: UUID,
        command_id: UUID,
        succeeded: bool,
        output: Any,
        error: str | None,
    ) -> None:
        result = DeviceCommandResult(
            command_id=command_id,
            device_id=device_id,
            succeeded=succeeded,
            output=output,
            error=error,
            completed_at=self._clock(),
        )
        resolved = await self._connections.resolve(device_id, session_id, result)
        if not resolved:
            raise CommandResultRejectedError(command_id)

    async def shutdown(self) -> None:
        await self._connections.close_all()

    async def _with_runtime_state(self, device: Device) -> Device:
        if device.status is DeviceStatus.REVOKED:
            return device
        connection = await self._connections.get_state(device.id)
        if connection is None:
            return replace(
                device,
                status=DeviceStatus.OFFLINE,
                advertised_capabilities=frozenset(),
            )
        return replace(
            device,
            status=connection.status,
            advertised_capabilities=connection.advertised_capabilities,
            last_seen_at=connection.last_seen_at,
        )
