from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import secrets
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.devices import Device, DeviceCapability
from app.domain.security import (
    DeviceCredential,
    DeviceIdentity,
    DevicePairing,
    DevicePairingChallenge,
)
from app.infrastructure.repositories.devices import (
    DeviceCredentialRecord,
    SqlAlchemyDeviceRegistry,
)
from app.infrastructure.security.credentials import (
    DeviceCredentialCodec,
    IssuedDeviceCredential,
)


Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]
TokenFactory = Callable[[], str]
SessionFactory = async_sessionmaker[AsyncSession]


class SqlAlchemyDeviceIdentity(DeviceIdentity):
    """Persist keyed device-token digests, never the bearer tokens themselves."""

    def __init__(
        self,
        session_factory: SessionFactory,
        codec: DeviceCredentialCodec,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._codec = codec
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def issue(self, device_id: UUID) -> str:
        issued = self.prepare()
        values = self.record_values(device_id, issued, self._now())
        async with self._session_factory() as session:
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "postgresql":
                insert_statement = postgresql_insert(
                    DeviceCredentialRecord
                ).values(**values)
            elif dialect_name == "sqlite":
                insert_statement = sqlite_insert(DeviceCredentialRecord).values(
                    **values
                )
            else:
                raise RuntimeError(
                    "Atomic device credential rotation is not implemented for "
                    f"'{dialect_name}'."
                )

            statement = insert_statement.on_conflict_do_update(
                index_elements=[DeviceCredentialRecord.device_id],
                set_={
                    "id": insert_statement.excluded.id,
                    "token_digest": insert_statement.excluded.token_digest,
                    "created_at": insert_statement.excluded.created_at,
                },
            )
            async with session.begin():
                await session.execute(statement)
        return issued.token

    async def authenticate(self, token: str) -> UUID | None:
        credential_id = self._codec.parse_token_id(token)
        if credential_id is None:
            return None
        statement = select(DeviceCredentialRecord).where(
            DeviceCredentialRecord.id == credential_id
        )
        async with self._session_factory() as session:
            record = await session.scalar(statement)
        if record is None or not self._codec.matches(token, record.token_digest):
            return None
        return record.device_id

    async def revoke(self, device_id: UUID) -> None:
        statement = delete(DeviceCredentialRecord).where(
            DeviceCredentialRecord.device_id == device_id
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(statement)

    def prepare(self) -> IssuedDeviceCredential:
        return self._codec.issue()

    @staticmethod
    def record_values(
        device_id: UUID,
        issued: IssuedDeviceCredential,
        created_at: datetime,
    ) -> dict[str, object]:
        return {
            "id": issued.id,
            "device_id": device_id,
            "token_digest": issued.digest,
            "created_at": created_at,
        }

    @classmethod
    def record_from_issued(
        cls,
        device_id: UUID,
        issued: IssuedDeviceCredential,
        created_at: datetime,
    ) -> DeviceCredentialRecord:
        return DeviceCredentialRecord(
            **cls.record_values(device_id, issued, created_at)
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("credential clock must return a timezone-aware value")
        return value


@dataclass(slots=True)
class _PairingRecord:
    secret_digest: bytes
    granted_capabilities: frozenset[DeviceCapability]
    expires_at: datetime
    failed_attempts: int = 0


class SqlAlchemyDevicePairing(DevicePairing):
    """Process-local challenges with atomic SQL device registration."""

    def __init__(
        self,
        session_factory: SessionFactory,
        identities: SqlAlchemyDeviceIdentity,
        *,
        clock: Clock | None = None,
        pairing_digest_key: bytes | None = None,
        id_factory: IdFactory = uuid4,
        token_factory: TokenFactory | None = None,
        max_claim_attempts: int = 5,
    ) -> None:
        if max_claim_attempts < 1:
            raise ValueError("Pairing claim attempts must be at least one.")
        if pairing_digest_key is not None and len(pairing_digest_key) < 32:
            raise ValueError("pairing digest key must contain at least 32 bytes")
        self._session_factory = session_factory
        self._identities = identities
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._digest_key = pairing_digest_key or secrets.token_bytes(32)
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
        now = self._now()
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
            now = self._now()
            if now >= record.expires_at:
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
                paired_at=now,
            )
            issued = self._identities.prepare()

            # The challenge remains claimable if flush or commit fails. Both
            # durable rows are committed together; no compensating writes are
            # needed and no partial registration can escape this transaction.
            async with self._session_factory() as session:
                async with session.begin():
                    device_record = (
                        SqlAlchemyDeviceRegistry.record_from_domain(device)
                    )
                    session.add(device_record)
                    # The mappers intentionally have no ORM relationship, so
                    # make the FK ordering explicit. This remains one atomic
                    # transaction and a later credential failure rolls the
                    # device insert back.
                    await session.flush()
                    session.add(
                        self._identities.record_from_issued(
                            device.id,
                            issued,
                            now,
                        )
                    )

            del self._pairings[pairing_id]
            return DeviceCredential(device=device, token=issued.token)

    def _digest(self, value: str) -> bytes:
        return hmac.digest(self._digest_key, value.encode("utf-8"), "sha256")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pairing clock must return a timezone-aware value")
        return value
