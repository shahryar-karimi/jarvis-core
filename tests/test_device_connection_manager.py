from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.domain.devices import (
    DeviceCapability,
    DeviceCapabilityUnavailableError,
    DeviceCommand,
    DeviceCommandResult,
    DeviceCommandTimeoutError,
    DeviceOfflineError,
    DeviceStatus,
    DeviceTransport,
)
from app.infrastructure.devices import InMemoryDeviceConnectionManager


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = UUID("60000000-0000-0000-0000-000000000006")
OTHER_DEVICE_ID = UUID("70000000-0000-0000-0000-000000000007")
COMMAND_ID = UUID("80000000-0000-0000-0000-000000000008")


@dataclass
class RecordingTransport(DeviceTransport):
    sent: list[tuple[DeviceCommand, UUID]] = field(default_factory=list)
    closes: list[tuple[int, str]] = field(default_factory=list)
    command_sent: asyncio.Event = field(default_factory=asyncio.Event)

    async def send_command(
        self,
        command: DeviceCommand,
        session_id: UUID,
    ) -> None:
        self.sent.append((command, session_id))
        self.command_sent.set()

    async def close(self, code: int, reason: str) -> None:
        self.closes.append((code, reason))


def make_command(
    capability: DeviceCapability = DeviceCapability.OPEN_APP,
) -> DeviceCommand:
    return DeviceCommand(
        id=COMMAND_ID,
        device_id=DEVICE_ID,
        capability=capability,
        action="open",
        arguments={"name": "PyCharm"},
        issued_at=NOW,
        deadline_at=NOW + timedelta(seconds=10),
    )


def successful_result(
    *,
    device_id: UUID = DEVICE_ID,
) -> DeviceCommandResult:
    return DeviceCommandResult(
        command_id=COMMAND_ID,
        device_id=device_id,
        succeeded=True,
        output={"opened": True},
        completed_at=NOW + timedelta(milliseconds=10),
    )


@pytest.mark.asyncio
async def test_activation_limits_dispatch_and_result_correlation() -> None:
    manager = InMemoryDeviceConnectionManager(clock=lambda: NOW)
    transport = RecordingTransport()
    session_id = await manager.connect(DEVICE_ID, transport)

    connecting = await manager.get_state(DEVICE_ID)
    assert connecting is not None
    assert connecting.status is DeviceStatus.CONNECTING

    await manager.activate(
        DEVICE_ID,
        session_id,
        frozenset({DeviceCapability.OPEN_APP, DeviceCapability.SYSTEM}),
        frozenset({DeviceCapability.OPEN_APP}),
    )
    online = await manager.get_state(DEVICE_ID)
    assert online is not None
    assert online.status is DeviceStatus.ONLINE
    assert online.advertised_capabilities == frozenset(
        {DeviceCapability.OPEN_APP, DeviceCapability.SYSTEM}
    )
    assert online.effective_capabilities == frozenset(
        {DeviceCapability.OPEN_APP}
    )

    unavailable = make_command(DeviceCapability.SYSTEM)
    with pytest.raises(DeviceCapabilityUnavailableError):
        await manager.execute(unavailable, timeout_seconds=1)
    assert transport.sent == []

    command = make_command()
    execution = asyncio.create_task(manager.execute(command, timeout_seconds=1))
    await asyncio.wait_for(transport.command_sent.wait(), timeout=0.5)

    assert transport.sent == [(command, session_id)]
    assert not await manager.resolve(
        OTHER_DEVICE_ID,
        session_id,
        successful_result(device_id=OTHER_DEVICE_ID),
    )
    assert not await manager.resolve(
        DEVICE_ID,
        UUID("90000000-0000-0000-0000-000000000009"),
        successful_result(),
    )
    assert not execution.done()

    result = successful_result()
    assert await manager.resolve(DEVICE_ID, session_id, result)
    assert await execution == result
    assert not await manager.resolve(DEVICE_ID, session_id, result)


@pytest.mark.asyncio
async def test_command_timeout_removes_pending_result() -> None:
    manager = InMemoryDeviceConnectionManager(clock=lambda: NOW)
    transport = RecordingTransport()
    session_id = await manager.connect(DEVICE_ID, transport)
    await manager.activate(
        DEVICE_ID,
        session_id,
        frozenset({DeviceCapability.OPEN_APP}),
        frozenset({DeviceCapability.OPEN_APP}),
    )
    command = make_command()

    with pytest.raises(DeviceCommandTimeoutError) as captured:
        await manager.execute(command, timeout_seconds=0.01)

    assert captured.value.command_id == command.id
    assert transport.sent == [(command, session_id)]
    assert not await manager.resolve(
        DEVICE_ID,
        session_id,
        successful_result(),
    )


@pytest.mark.asyncio
async def test_replaced_session_cannot_disconnect_the_new_connection() -> None:
    manager = InMemoryDeviceConnectionManager(clock=lambda: NOW)
    old_transport = RecordingTransport()
    new_transport = RecordingTransport()

    old_session = await manager.connect(DEVICE_ID, old_transport)
    await manager.activate(
        DEVICE_ID,
        old_session,
        frozenset({DeviceCapability.OPEN_APP}),
        frozenset({DeviceCapability.OPEN_APP}),
    )
    new_session = await manager.connect(DEVICE_ID, new_transport)

    assert new_session != old_session
    assert old_transport.closes == [
        (
            manager.REPLACED_CLOSE_CODE,
            "Connection replaced by a newer session.",
        )
    ]
    assert not await manager.disconnect(DEVICE_ID, old_session)

    current = await manager.get_state(DEVICE_ID)
    assert current is not None
    assert current.session_id == new_session
    assert current.status is DeviceStatus.CONNECTING

    await manager.activate(
        DEVICE_ID,
        new_session,
        frozenset({DeviceCapability.SYSTEM}),
        frozenset({DeviceCapability.SYSTEM}),
    )
    assert await manager.disconnect(DEVICE_ID, new_session)
    assert await manager.get_state(DEVICE_ID) is None


@pytest.mark.asyncio
async def test_shutdown_closes_transport_and_fails_pending_command() -> None:
    manager = InMemoryDeviceConnectionManager(clock=lambda: NOW)
    transport = RecordingTransport()
    session_id = await manager.connect(DEVICE_ID, transport)
    await manager.activate(
        DEVICE_ID,
        session_id,
        frozenset({DeviceCapability.OPEN_APP}),
        frozenset({DeviceCapability.OPEN_APP}),
    )
    execution = asyncio.create_task(
        manager.execute(make_command(), timeout_seconds=1)
    )
    await asyncio.wait_for(transport.command_sent.wait(), timeout=0.5)

    await manager.close_all()

    with pytest.raises(DeviceOfflineError):
        await execution
    assert transport.closes == [(1001, "Application shutting down.")]
    assert await manager.get_state(DEVICE_ID) is None


@pytest.mark.asyncio
async def test_failed_transport_send_removes_the_connection() -> None:
    class FailingTransport(RecordingTransport):
        async def send_command(
            self,
            command: DeviceCommand,
            session_id: UUID,
        ) -> None:
            raise RuntimeError("socket send failed")

    manager = InMemoryDeviceConnectionManager(clock=lambda: NOW)
    transport = FailingTransport()
    session_id = await manager.connect(DEVICE_ID, transport)
    await manager.activate(
        DEVICE_ID,
        session_id,
        frozenset({DeviceCapability.OPEN_APP}),
        frozenset({DeviceCapability.OPEN_APP}),
    )

    with pytest.raises(RuntimeError, match="socket send failed"):
        await manager.execute(make_command(), timeout_seconds=1)

    assert await manager.get_state(DEVICE_ID) is None
