from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import json
import logging
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.websocket.protocol import (
    DEVICE_SUBPROTOCOL,
    CapabilitiesUpdateMessage,
    CommandResultMessage,
    DeviceHelloMessage,
    HeartbeatPongMessage,
    device_message_adapter,
    serialize_datetime,
    server_message,
)
from app.application.devices import (
    CommandResultRejectedError,
    DeviceAuthenticationError,
    DeviceService,
)
from app.core.config import Settings
from app.domain.devices import DeviceCommand, DeviceError, DeviceTransport
from app.infrastructure.hive import HiveResources


router = APIRouter(prefix="/devices", tags=["device gateway"])
logger = logging.getLogger(__name__)


class _ProtocolClosed(Exception):
    pass


class WebSocketDeviceTransport(DeviceTransport):
    """Serializes domain commands onto one authenticated WebSocket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._send_lock = asyncio.Lock()
        self._closed = False

    async def send_command(
        self,
        command: DeviceCommand,
        session_id: UUID,
    ) -> None:
        await self.send_message(
            "command.execute",
            session_id,
            {
                "command_id": str(command.id),
                "capability": command.capability.value,
                "action": command.action,
                "arguments": command.arguments,
                "issued_at": serialize_datetime(command.issued_at),
                "deadline_at": serialize_datetime(command.deadline_at),
            },
        )

    async def send_message(
        self,
        message_type: str,
        session_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        async with self._send_lock:
            if self._closed:
                raise RuntimeError("device transport is closed")
            await self._websocket.send_json(
                server_message(message_type, session_id, payload)
            )

    async def close(self, code: int, reason: str) -> None:
        async with self._send_lock:
            if self._closed:
                return
            self._closed = True
            with suppress(RuntimeError, WebSocketDisconnect):
                await self._websocket.close(code=code, reason=reason[:123])


@router.websocket("/ws")
async def device_gateway(websocket: WebSocket) -> None:
    resources = cast(HiveResources | None, getattr(websocket.app.state, "hive", None))
    settings = cast(Settings, websocket.app.state.settings)
    if resources is None:
        await websocket.close(code=1013, reason="Gateway unavailable")
        return
    service = resources.device_service

    if not _supports_protocol(websocket):
        await websocket.close(code=4406, reason="Protocol version required")
        return
    token = _bearer_token(websocket)
    if token is None:
        await websocket.close(code=4403, reason="Authentication failed")
        return
    try:
        device = await service.authenticate(token)
    except DeviceAuthenticationError:
        await websocket.close(code=4403, reason="Authentication failed")
        return

    await websocket.accept(subprotocol=DEVICE_SUBPROTOCOL)
    transport = WebSocketDeviceTransport(websocket)
    session_id: UUID | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    heartbeat_nonce: UUID | None = None
    heartbeat_received = asyncio.Event()
    seen_ids: deque[UUID] = deque(maxlen=1_024)
    seen_id_set: set[UUID] = set()

    try:
        session_id = await service.connect_device(device.id, transport)
        await transport.send_message(
            "server.hello",
            session_id,
            {
                "device_id": str(device.id),
                "heartbeat_interval_seconds": (
                    settings.device_heartbeat_interval_seconds
                ),
                "max_message_bytes": settings.device_max_message_bytes,
            },
        )
        try:
            hello = await asyncio.wait_for(
                _receive_message(websocket, settings.device_max_message_bytes),
                timeout=10.0,
            )
        except TimeoutError:
            await transport.close(4408, "Device hello timed out")
            return
        if not isinstance(hello, DeviceHelloMessage):
            await _protocol_error(
                transport,
                session_id,
                "device_hello_required",
                "device.hello must be the first message",
            )
            return
        _validate_session(hello.session_id, session_id)
        _remember_message(hello.message_id, seen_ids, seen_id_set)
        effective = await service.activate_device(
            device.id,
            session_id,
            frozenset(hello.payload.capabilities),
        )
        await transport.send_message(
            "server.ready",
            session_id,
            {
                "device_id": str(device.id),
                "effective_capabilities": sorted(
                    capability.value for capability in effective
                ),
            },
        )

        async def heartbeat_loop() -> None:
            nonlocal heartbeat_nonce
            interval = settings.device_heartbeat_interval_seconds
            while True:
                await asyncio.sleep(interval)
                heartbeat_received.clear()
                heartbeat_nonce = uuid4()
                await transport.send_message(
                    "heartbeat.ping",
                    session_id,
                    {"nonce": str(heartbeat_nonce)},
                )
                try:
                    await asyncio.wait_for(
                        heartbeat_received.wait(),
                        timeout=max(1.0, interval / 2),
                    )
                except TimeoutError:
                    await transport.close(4408, "Heartbeat timed out")
                    return

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        while True:
            message = await _receive_message(
                websocket,
                settings.device_max_message_bytes,
            )
            _validate_session(message.session_id, session_id)
            _remember_message(message.message_id, seen_ids, seen_id_set)

            if isinstance(message, HeartbeatPongMessage):
                if heartbeat_nonce is None or message.payload.nonce != heartbeat_nonce:
                    raise ValueError("unexpected heartbeat nonce")
                await service.mark_seen(device.id, session_id)
                heartbeat_nonce = None
                heartbeat_received.set()
                continue
            if isinstance(message, CapabilitiesUpdateMessage):
                effective = await service.activate_device(
                    device.id,
                    session_id,
                    frozenset(message.payload.capabilities),
                )
                await transport.send_message(
                    "capabilities.ack",
                    session_id,
                    {
                        "effective_capabilities": sorted(
                            capability.value for capability in effective
                        )
                    },
                )
                continue
            if isinstance(message, CommandResultMessage):
                try:
                    await service.accept_command_result(
                        device.id,
                        session_id,
                        message.payload.command_id,
                        message.payload.succeeded,
                        message.payload.output,
                        message.payload.error,
                    )
                except CommandResultRejectedError as error:
                    await transport.send_message(
                        "command.result_rejected",
                        session_id,
                        {
                            "command_id": str(error.command_id),
                            "reason": "command_is_not_pending",
                        },
                    )
                continue
            raise ValueError("message is not valid in the ready state")
    except WebSocketDisconnect:
        pass
    except (DeviceError, ValidationError, ValueError, json.JSONDecodeError):
        if session_id is not None:
            await _protocol_error(
                transport,
                session_id,
                "invalid_message",
                "Message does not match the device protocol",
            )
    except _ProtocolClosed:
        pass
    except Exception:
        logger.exception("Device gateway failed for device %s", device.id)
        await transport.close(1011, "Internal gateway error")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        if session_id is not None:
            await service.disconnect_device_session(device.id, session_id)


def _supports_protocol(websocket: WebSocket) -> bool:
    requested = websocket.headers.get("sec-websocket-protocol", "")
    return DEVICE_SUBPROTOCOL in {
        protocol.strip() for protocol in requested.split(",") if protocol.strip()
    }


def _bearer_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        return None
    return token.strip()


async def _receive_message(websocket: WebSocket, max_bytes: int):
    event = await websocket.receive()
    if event["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=event.get("code", 1000),
            reason=event.get("reason", ""),
        )
    if event.get("bytes") is not None:
        await websocket.close(code=1003, reason="Text JSON messages are required")
        raise _ProtocolClosed
    raw = event.get("text", "")
    if len(raw.encode("utf-8")) > max_bytes:
        await websocket.close(code=1009, reason="Message is too large")
        raise _ProtocolClosed
    return device_message_adapter.validate_python(json.loads(raw))


def _validate_session(received: UUID, expected: UUID) -> None:
    if received != expected:
        raise ValueError("message session does not match the connection")


def _remember_message(
    message_id: UUID,
    ordered: deque[UUID],
    seen: set[UUID],
) -> None:
    if message_id in seen:
        raise ValueError("message ID was replayed")
    if len(ordered) == ordered.maxlen:
        seen.discard(ordered[0])
    ordered.append(message_id)
    seen.add(message_id)


async def _protocol_error(
    transport: WebSocketDeviceTransport,
    session_id: UUID,
    code: str,
    detail: str,
) -> None:
    with suppress(RuntimeError, WebSocketDisconnect):
        await transport.send_message(
            "protocol.error",
            session_id,
            {"code": code, "detail": detail},
        )
    await transport.close(4400, "Invalid device protocol message")
