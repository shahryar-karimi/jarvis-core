from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.devices import (
    CapabilityDefinition,
    CapabilityRegistry,
    Device,
    DeviceCapability,
    DeviceCapabilityUnavailableError,
    DeviceCommand,
    DeviceCommandResult,
    DeviceCommandTimeoutError,
    DeviceConnectionManager,
    DeviceConnectionState,
    DeviceNotFoundError,
    DeviceOfflineError,
    DeviceRegistry,
    DeviceStatus,
    DeviceTransport,
)

Clock = Callable[[], datetime]


class InMemoryCapabilityRegistry(CapabilityRegistry):
    """Allowlisted capability/action definitions shipped by Core."""

    def __init__(
        self,
        definitions: Collection[CapabilityDefinition] | None = None,
    ) -> None:
        configured = definitions or (
            CapabilityDefinition(DeviceCapability.OPEN_APP, frozenset({"open"})),
            CapabilityDefinition(
                DeviceCapability.FILES,
                frozenset({"list", "read", "write"}),
            ),
            CapabilityDefinition(
                DeviceCapability.SYSTEM,
                frozenset({"get_info", "get_status"}),
            ),
        )
        self._definitions = {
            definition.capability: deepcopy(definition) for definition in configured
        }

    async def list_all(self) -> list[CapabilityDefinition]:
        return deepcopy(
            sorted(
                self._definitions.values(),
                key=lambda definition: definition.capability.value,
            )
        )

    async def allows(self, capability: DeviceCapability, action: str) -> bool:
        definition = self._definitions.get(capability)
        return definition is not None and action in definition.actions


class InMemoryDeviceRegistry(DeviceRegistry):
    """Concurrency-safe device storage for one Core process."""

    def __init__(self, devices: Collection[Device] = ()) -> None:
        self._devices = {device.id: deepcopy(device) for device in devices}
        self._lock = asyncio.Lock()

    async def add(self, device: Device) -> None:
        async with self._lock:
            if device.id in self._devices:
                raise ValueError(f"Device '{device.id}' is already registered.")
            self._devices[device.id] = deepcopy(device)

    async def get(self, device_id: UUID) -> Device | None:
        async with self._lock:
            device = self._devices.get(device_id)
            return deepcopy(device) if device is not None else None

    async def list_all(self) -> list[Device]:
        async with self._lock:
            devices = sorted(
                self._devices.values(),
                key=lambda device: (device.paired_at, str(device.id)),
            )
            return deepcopy(devices)

    async def save(self, device: Device) -> None:
        async with self._lock:
            if device.id not in self._devices:
                raise DeviceNotFoundError(device.id)
            self._devices[device.id] = deepcopy(device)

    async def remove(self, device_id: UUID) -> bool:
        async with self._lock:
            return self._devices.pop(device_id, None) is not None


@dataclass(slots=True)
class _LiveConnection:
    session_id: UUID
    transport: DeviceTransport
    status: DeviceStatus
    advertised_capabilities: frozenset[DeviceCapability]
    effective_capabilities: frozenset[DeviceCapability]
    connected_at: datetime
    last_seen_at: datetime
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(slots=True)
class _PendingCommand:
    device_id: UUID
    session_id: UUID
    future: asyncio.Future[DeviceCommandResult]


class InMemoryDeviceConnectionManager(DeviceConnectionManager):
    """Routes commands to transports owned by this Core process."""

    REPLACED_CLOSE_CODE = 4409

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._connections: dict[UUID, _LiveConnection] = {}
        self._pending: dict[UUID, _PendingCommand] = {}
        self._lock = asyncio.Lock()

    async def connect(self, device_id: UUID, transport: DeviceTransport) -> UUID:
        now = self._clock()
        connection = _LiveConnection(
            session_id=uuid4(),
            transport=transport,
            status=DeviceStatus.CONNECTING,
            advertised_capabilities=frozenset(),
            effective_capabilities=frozenset(),
            connected_at=now,
            last_seen_at=now,
        )
        async with self._lock:
            replaced = self._connections.get(device_id)
            self._connections[device_id] = connection
            pending = self._remove_pending_for_device(device_id)
        self._fail_pending(pending, DeviceOfflineError(device_id))
        if replaced is not None:
            try:
                await replaced.transport.close(
                    self.REPLACED_CLOSE_CODE,
                    "Connection replaced by a newer session.",
                )
            except Exception:
                pass
        return connection.session_id

    async def activate(
        self,
        device_id: UUID,
        session_id: UUID,
        advertised_capabilities: frozenset[DeviceCapability],
        effective_capabilities: frozenset[DeviceCapability],
    ) -> None:
        if not effective_capabilities <= advertised_capabilities:
            raise ValueError("effective capabilities must be advertised by the device")
        async with self._lock:
            connection = self._current(device_id, session_id)
            connection.status = DeviceStatus.ONLINE
            connection.advertised_capabilities = frozenset(advertised_capabilities)
            connection.effective_capabilities = frozenset(effective_capabilities)
            connection.last_seen_at = self._clock()

    async def disconnect(self, device_id: UUID, session_id: UUID) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.session_id != session_id:
                return False
            del self._connections[device_id]
            pending = self._remove_pending_for_device(device_id)
        self._fail_pending(pending, DeviceOfflineError(device_id))
        return True

    async def disconnect_device(
        self,
        device_id: UUID,
        code: int,
        reason: str,
    ) -> None:
        async with self._lock:
            connection = self._connections.pop(device_id, None)
            pending = self._remove_pending_for_device(device_id)
        self._fail_pending(pending, DeviceOfflineError(device_id))
        if connection is not None:
            try:
                await connection.transport.close(code, reason)
            except Exception:
                pass

    async def get_state(self, device_id: UUID) -> DeviceConnectionState | None:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None:
                return None
            return DeviceConnectionState(
                session_id=connection.session_id,
                status=connection.status,
                advertised_capabilities=connection.advertised_capabilities,
                effective_capabilities=connection.effective_capabilities,
                connected_at=connection.connected_at,
                last_seen_at=connection.last_seen_at,
            )

    async def touch(self, device_id: UUID, session_id: UUID) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.session_id != session_id:
                return False
            connection.last_seen_at = self._clock()
            return True

    async def execute(
        self,
        command: DeviceCommand,
        timeout_seconds: float,
    ) -> DeviceCommandResult:
        if timeout_seconds <= 0:
            raise ValueError("command timeout must be greater than zero")
        deadline_seconds = (command.deadline_at - self._clock()).total_seconds()
        if deadline_seconds <= 0:
            raise DeviceCommandTimeoutError(command.id)
        future: asyncio.Future[DeviceCommandResult] = asyncio.get_running_loop().create_future()
        async with self._lock:
            connection = self._connections.get(command.device_id)
            if connection is None or connection.status is not DeviceStatus.ONLINE:
                raise DeviceOfflineError(command.device_id)
            if command.capability not in connection.effective_capabilities:
                raise DeviceCapabilityUnavailableError(
                    command.device_id,
                    command.capability,
                )
            if command.id in self._pending:
                raise ValueError(f"Command '{command.id}' is already pending.")
            pending = _PendingCommand(
                device_id=command.device_id,
                session_id=connection.session_id,
                future=future,
            )
            self._pending[command.id] = pending

        try:
            async with connection.send_lock:
                async with self._lock:
                    if self._connections.get(command.device_id) is not connection:
                        raise DeviceOfflineError(command.device_id)
                try:
                    await connection.transport.send_command(
                        command,
                        connection.session_id,
                    )
                except Exception:
                    await self.disconnect(
                        command.device_id,
                        connection.session_id,
                    )
                    raise
            deadline_seconds = (command.deadline_at - self._clock()).total_seconds()
            effective_timeout = min(timeout_seconds, max(0.0, deadline_seconds))
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=effective_timeout,
                )
            except TimeoutError as error:
                raise DeviceCommandTimeoutError(command.id) from error
        finally:
            async with self._lock:
                if self._pending.get(command.id) is pending:
                    del self._pending[command.id]
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def resolve(
        self,
        device_id: UUID,
        session_id: UUID,
        result: DeviceCommandResult,
    ) -> bool:
        async with self._lock:
            pending = self._pending.get(result.command_id)
            connection = self._connections.get(device_id)
            if (
                pending is None
                or pending.device_id != device_id
                or pending.session_id != session_id
                or result.device_id != device_id
                or connection is None
                or connection.session_id != session_id
            ):
                return False
            if pending.future.done():
                del self._pending[result.command_id]
                return False
            del self._pending[result.command_id]
            pending.future.set_result(result)
            return True

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.items())
            self._connections.clear()
            pending = list(self._pending.values())
            self._pending.clear()
        for device_id, _ in connections:
            self._fail_pending(
                [item for item in pending if item.device_id == device_id],
                DeviceOfflineError(device_id),
            )
        await asyncio.gather(
            *(
                connection.transport.close(1001, "Application shutting down.")
                for _, connection in connections
            ),
            return_exceptions=True,
        )

    def _current(self, device_id: UUID, session_id: UUID) -> _LiveConnection:
        connection = self._connections.get(device_id)
        if connection is None or connection.session_id != session_id:
            raise DeviceOfflineError(device_id)
        return connection

    def _remove_pending_for_device(
        self,
        device_id: UUID,
    ) -> list[_PendingCommand]:
        command_ids = [
            command_id
            for command_id, pending in self._pending.items()
            if pending.device_id == device_id
        ]
        return [self._pending.pop(command_id) for command_id in command_ids]

    @staticmethod
    def _fail_pending(
        pending_commands: Collection[_PendingCommand],
        error: Exception,
    ) -> None:
        for pending in pending_commands:
            if not pending.future.done():
                pending.future.set_exception(error)
