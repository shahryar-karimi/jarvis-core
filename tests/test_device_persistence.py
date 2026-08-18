from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.domain.devices import (
    Device,
    DeviceCapability,
    DeviceNotFoundError,
    DeviceStatus,
)
from app.infrastructure.database import Database
from app.infrastructure.repositories.devices import (
    DeviceCredentialRecord,
    DeviceRecord,
    SqlAlchemyDeviceRegistry,
)
from app.infrastructure.security import (
    DeviceCredentialCodec,
    SqlAlchemyDeviceIdentity,
    SqlAlchemyDevicePairing,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
PAIRING_ID = UUID("10000000-0000-0000-0000-000000000001")
DEVICE_ID = UUID("20000000-0000-0000-0000-000000000002")
EXISTING_DEVICE_ID = UUID("30000000-0000-0000-0000-000000000003")
CREDENTIAL_ID = UUID("40000000-0000-0000-0000-000000000004")
ROTATED_CREDENTIAL_ID = UUID("50000000-0000-0000-0000-000000000005")
DIGEST_KEY = b"persistent-device-credential-key"
PAIRING_SECRET = "pairing-secret-with-enough-entropy"
CREDENTIAL_SECRET = "credential-secret-with-enough-entropy"
ROTATED_CREDENTIAL_SECRET = "rotated-credential-secret-with-enough-entropy"


class SequenceIds:
    def __init__(self, *values: UUID) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        return next(self._values)


async def migrated_database(tmp_path: Path, name: str) -> Database:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database(database_url)

    @event.listens_for(database.engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return database


def device(
    device_id: UUID = DEVICE_ID,
    *,
    name: str = "Office workstation",
) -> Device:
    return Device(
        id=device_id,
        name=name,
        granted_capabilities=frozenset(
            {DeviceCapability.OPEN_APP, DeviceCapability.SYSTEM}
        ),
        paired_at=NOW,
    )


@pytest.mark.asyncio
async def test_device_registry_round_trips_durable_state_across_instances(
    tmp_path: Path,
) -> None:
    database = await migrated_database(tmp_path, "registry.db")
    try:
        registry = SqlAlchemyDeviceRegistry(database.session_factory)
        registered = replace(
            device(),
            status=DeviceStatus.ONLINE,
            advertised_capabilities=frozenset({DeviceCapability.OPEN_APP}),
            last_seen_at=NOW + timedelta(minutes=1),
        )
        await registry.add(registered)

        fresh_registry = SqlAlchemyDeviceRegistry(database.session_factory)
        durable = replace(
            registered,
            status=DeviceStatus.OFFLINE,
            advertised_capabilities=frozenset(),
            last_seen_at=None,
        )
        assert await fresh_registry.get(registered.id) == durable
        assert await fresh_registry.list_all() == [durable]
        with pytest.raises(ValueError, match="already registered"):
            await fresh_registry.add(durable)
        with pytest.raises(DeviceNotFoundError):
            await fresh_registry.save(device(EXISTING_DEVICE_ID))

        revoked_at = NOW + timedelta(hours=1)
        revoked = replace(
            durable,
            status=DeviceStatus.REVOKED,
            revoked_at=revoked_at,
        )
        await fresh_registry.save(revoked)

        another_registry = SqlAlchemyDeviceRegistry(database.session_factory)
        assert await another_registry.get(registered.id) == revoked
        assert await another_registry.remove(registered.id) is True
        assert await another_registry.remove(registered.id) is False
        assert await another_registry.get(registered.id) is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_pairing_persists_only_a_digest_and_survives_resource_restart(
    tmp_path: Path,
) -> None:
    database = await migrated_database(tmp_path, "pairing.db")
    try:
        codec = DeviceCredentialCodec(
            DIGEST_KEY,
            id_factory=lambda: CREDENTIAL_ID,
            token_factory=lambda: CREDENTIAL_SECRET,
        )
        identity = SqlAlchemyDeviceIdentity(
            database.session_factory,
            codec,
            clock=lambda: NOW,
        )
        pairing = SqlAlchemyDevicePairing(
            database.session_factory,
            identity,
            clock=lambda: NOW,
            pairing_digest_key=b"process-local-pairing-digest-key",
            id_factory=SequenceIds(PAIRING_ID, DEVICE_ID),
            token_factory=lambda: PAIRING_SECRET,
        )
        challenge = await pairing.create(
            frozenset({DeviceCapability.OPEN_APP}),
            timedelta(minutes=5),
        )

        credential = await pairing.claim(
            challenge.id,
            challenge.secret,
            "Office workstation",
        )

        assert credential is not None
        assert credential.device.id == DEVICE_ID
        assert await pairing.claim(
            challenge.id,
            challenge.secret,
            "Replay",
        ) is None

        async with database.session_factory() as session:
            stored = (
                await session.scalars(select(DeviceCredentialRecord))
            ).one()
        assert stored.id == CREDENTIAL_ID
        assert len(stored.token_digest) == 32
        assert credential.token.encode("utf-8") != stored.token_digest

        # Reconstruct every security resource as a process restart would. The
        # stable deployment key keeps the previously issued token valid.
        restarted_identity = SqlAlchemyDeviceIdentity(
            database.session_factory,
            DeviceCredentialCodec(DIGEST_KEY),
        )
        restarted_registry = SqlAlchemyDeviceRegistry(database.session_factory)
        assert await restarted_identity.authenticate(credential.token) == DEVICE_ID
        assert await restarted_identity.authenticate(
            f"{credential.token}tampered"
        ) is None
        assert await restarted_registry.get(DEVICE_ID) == credential.device

        rotating_identity = SqlAlchemyDeviceIdentity(
            database.session_factory,
            DeviceCredentialCodec(
                DIGEST_KEY,
                id_factory=lambda: ROTATED_CREDENTIAL_ID,
                token_factory=lambda: ROTATED_CREDENTIAL_SECRET,
            ),
            clock=lambda: NOW + timedelta(hours=1),
        )
        rotated_token = await rotating_identity.issue(DEVICE_ID)
        assert await restarted_identity.authenticate(credential.token) is None
        assert await restarted_identity.authenticate(rotated_token) == DEVICE_ID
        async with database.session_factory() as session:
            credential_count = await session.scalar(
                select(func.count()).select_from(DeviceCredentialRecord)
            )
        assert credential_count == 1

        await restarted_identity.revoke(DEVICE_ID)
        assert await restarted_identity.authenticate(credential.token) is None
        assert await restarted_identity.authenticate(rotated_token) is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_pairing_database_failure_rolls_back_both_rows_and_keeps_challenge(
    tmp_path: Path,
) -> None:
    database = await migrated_database(tmp_path, "pairing-failure.db")
    try:
        registry = SqlAlchemyDeviceRegistry(database.session_factory)
        await registry.add(device(EXISTING_DEVICE_ID, name="Existing device"))

        codec = DeviceCredentialCodec(
            DIGEST_KEY,
            id_factory=lambda: CREDENTIAL_ID,
            token_factory=lambda: CREDENTIAL_SECRET,
        )
        identity = SqlAlchemyDeviceIdentity(
            database.session_factory,
            codec,
            clock=lambda: NOW,
        )
        await identity.issue(EXISTING_DEVICE_ID)

        pairing = SqlAlchemyDevicePairing(
            database.session_factory,
            identity,
            clock=lambda: NOW,
            pairing_digest_key=b"process-local-pairing-digest-key",
            id_factory=SequenceIds(PAIRING_ID, DEVICE_ID),
            token_factory=lambda: PAIRING_SECRET,
        )
        challenge = await pairing.create(
            frozenset({DeviceCapability.FILES}),
            timedelta(minutes=5),
        )

        # The fixed codec deliberately collides with the existing credential
        # primary key. The device insert in the same transaction must roll back.
        with pytest.raises(IntegrityError):
            await pairing.claim(
                challenge.id,
                challenge.secret,
                "Uncommitted device",
            )

        assert challenge.id in pairing._pairings
        assert await registry.get(DEVICE_ID) is None
        async with database.session_factory() as session:
            device_count = await session.scalar(
                select(func.count()).select_from(DeviceRecord)
            )
            credential_count = await session.scalar(
                select(func.count()).select_from(DeviceCredentialRecord)
            )
        assert device_count == 1
        assert credential_count == 1
    finally:
        await database.dispose()
