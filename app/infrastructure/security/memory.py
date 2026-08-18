from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import secrets
from uuid import UUID, uuid4

from app.domain.devices import Device, DeviceCapability, DeviceRegistry
from app.domain.security import (
    DeviceCredential,
    DeviceIdentity,
    DevicePairing,
    DevicePairingChallenge,
)
from app.infrastructure.security.credentials import DeviceCredentialCodec


Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]
TokenFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _CredentialRecord:
    device_id: UUID
    digest: bytes


class InMemoryDeviceIdentity(DeviceIdentity):
    """Issues opaque credentials while retaining only keyed token digests."""

    TOKEN_PREFIX = "jv1"

    def __init__(
        self,
        *,
        digest_key: bytes | None = None,
        id_factory: IdFactory = uuid4,
        token_factory: TokenFactory | None = None,
    ) -> None:
        self._codec = DeviceCredentialCodec(
            digest_key or secrets.token_bytes(32),
            id_factory=id_factory,
            token_factory=token_factory,
        )
        self._credentials: dict[UUID, _CredentialRecord] = {}
        self._device_tokens: dict[UUID, set[UUID]] = {}
        self._lock = asyncio.Lock()

    async def issue(self, device_id: UUID) -> str:
        issued = self._codec.issue()
        record = _CredentialRecord(device_id=device_id, digest=issued.digest)

        async with self._lock:
            if issued.id in self._credentials:
                raise RuntimeError("device token identifier collision")
            # Issuing a new credential rotates every previous credential for
            # this device, so a lost token cannot remain valid indefinitely.
            for previous_id in self._device_tokens.pop(device_id, set()):
                self._credentials.pop(previous_id, None)
            self._credentials[issued.id] = record
            self._device_tokens[device_id] = {issued.id}
        return issued.token

    async def authenticate(self, token: str) -> UUID | None:
        token_id = self._parse_token_id(token)
        if token_id is None:
            return None

        async with self._lock:
            record = self._credentials.get(token_id)
            if record is None:
                return None
            if not self._codec.matches(token, record.digest):
                return None
            return record.device_id

    async def revoke(self, device_id: UUID) -> None:
        async with self._lock:
            for token_id in self._device_tokens.pop(device_id, set()):
                self._credentials.pop(token_id, None)

    def _digest(self, value: str) -> bytes:
        return self._codec.digest(value)

    @classmethod
    def _parse_token_id(cls, token: str) -> UUID | None:
        return DeviceCredentialCodec.parse_token_id(token)


@dataclass(slots=True)
class _PairingRecord:
    secret_digest: bytes
    granted_capabilities: frozenset[DeviceCapability]
    expires_at: datetime
    failed_attempts: int = 0


class InMemoryDevicePairing(DevicePairing):
    """Single-process, atomic one-time pairing for the initial gateway."""

    def __init__(
        self,
        devices: DeviceRegistry,
        identities: DeviceIdentity,
        *,
        clock: Clock | None = None,
        digest_key: bytes | None = None,
        id_factory: IdFactory = uuid4,
        token_factory: TokenFactory | None = None,
        max_claim_attempts: int = 5,
    ) -> None:
        if max_claim_attempts < 1:
            raise ValueError("Pairing claim attempts must be at least one.")
        if digest_key is not None and len(digest_key) < 32:
            raise ValueError("pairing digest key must contain at least 32 bytes")
        self._devices = devices
        self._identities = identities
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._digest_key = digest_key or secrets.token_bytes(32)
        self._id_factory = id_factory
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._max_claim_attempts = max_claim_attempts
        self._pairings: dict[UUID, _PairingRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        granted_capabilities: frozenset[DeviceCapability],
        ttl: timedelta,
    ) -> DevicePairingChallenge:
        if ttl <= timedelta(0):
            raise ValueError("Pairing time-to-live must be greater than zero.")

        pairing_id = self._id_factory()
        secret = self._token_factory()
        if len(secret) < 16:
            raise ValueError("pairing secret is too short")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("pairing clock must return a timezone-aware value")
        expires_at = now + ttl
        record = _PairingRecord(
            secret_digest=self._digest(secret),
            granted_capabilities=frozenset(granted_capabilities),
            expires_at=expires_at,
        )
        async with self._lock:
            if pairing_id in self._pairings:
                raise RuntimeError("pairing identifier collision")
            self._pairings[pairing_id] = record

        return DevicePairingChallenge(
            id=pairing_id,
            secret=secret,
            granted_capabilities=record.granted_capabilities,
            expires_at=expires_at,
        )

    async def claim(
        self,
        pairing_id: UUID,
        secret: str,
        device_name: str,
    ) -> DeviceCredential | None:
        async with self._lock:
            record = self._pairings.get(pairing_id)
            if record is None:
                return None
            if self._clock() >= record.expires_at:
                del self._pairings[pairing_id]
                return None
            if not hmac.compare_digest(record.secret_digest, self._digest(secret)):
                record.failed_attempts += 1
                if record.failed_attempts >= self._max_claim_attempts:
                    del self._pairings[pairing_id]
                return None

            device = Device(
                id=self._id_factory(),
                name=device_name,
                granted_capabilities=record.granted_capabilities,
                paired_at=self._clock(),
            )
            token = await self._identities.issue(device.id)
            try:
                await self._devices.add(device)
            except BaseException:
                # Cancellation can arrive between the two awaited mutations.
                # Roll both stores back before allowing the challenge to retry.
                try:
                    await self._identities.revoke(device.id)
                finally:
                    await self._devices.remove(device.id)
                raise
            del self._pairings[pairing_id]
            return DeviceCredential(device=device, token=token)

    def _digest(self, value: str) -> bytes:
        return hmac.digest(self._digest_key, value.encode("utf-8"), "sha256")
