"""Opt-in integration tests against the PostgreSQL configured in .env.

Run with:
    Set RUN_DB_TESTS=1 in .env, then run:
    python -m pytest -m integration tests/test_database_integration.py -v

PowerShell:
    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration tests/test_database_integration.py -v
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.domain.devices import DeviceCapability
from app.domain.memory import MemoryCategory, MemoryEntry
from app.infrastructure.database import Database
from app.infrastructure.repositories.devices import (
    DeviceCredentialRecord,
    SqlAlchemyDeviceRegistry,
)
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository
from app.infrastructure.security import (
    DeviceCredentialCodec,
    SqlAlchemyDeviceIdentity,
    SqlAlchemyDevicePairing,
)

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def require_database_tests(external_test_settings) -> None:
    if not external_test_settings.run_db_tests:
        pytest.skip("Set RUN_DB_TESTS=1 in .env to test the configured PostgreSQL database")


@pytest.mark.asyncio
async def test_postgres_is_migrated_to_the_expected_schema(
    external_test_settings,
) -> None:
    require_database_tests(external_test_settings)

    database = Database(get_settings().database_url)
    try:
        async with database.engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1

            def inspect_schema(sync_connection):
                schema = inspect(sync_connection)
                return (
                    set(schema.get_table_names()),
                    {item["name"] for item in schema.get_indexes("memories")},
                    {item["name"] for item in schema.get_unique_constraints("memories")},
                    {item["name"] for item in schema.get_unique_constraints("device_credentials")},
                )

            (
                table_names,
                index_names,
                constraint_names,
                credential_constraint_names,
            ) = await connection.run_sync(inspect_schema)
            revisions = set(
                (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars()
            )

        assert "memories" in table_names
        assert "devices" in table_names
        assert "device_credentials" in table_names
        assert "ix_memories_category" in index_names
        assert "uq_memory_category_key" in constraint_names
        assert credential_constraint_names == {"uq_device_credentials_device_id"}
        migration_scripts = ScriptDirectory.from_config(Config(PROJECT_ROOT / "alembic.ini"))
        assert revisions == set(migration_scripts.get_heads())
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_fresh_postgres_migration_supports_atomic_persistence(
    external_test_settings,
) -> None:
    require_database_tests(external_test_settings)
    configured_url = make_url(get_settings().database_url)
    if not configured_url.drivername.startswith("postgresql"):
        pytest.fail("The integration database must be PostgreSQL")

    database_name = f"jarvis_test_{uuid4().hex}"
    admin_url = configured_url.set(database="postgres")
    test_url = configured_url.set(database=database_name)
    admin_engine = create_async_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    database_created = False

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        migration_config = Config(PROJECT_ROOT / "alembic.ini")
        migration_config.attributes["database_url"] = test_url.render_as_string(hide_password=False)
        await asyncio.to_thread(command.upgrade, migration_config, "head")

        database = Database(test_url.render_as_string(hide_password=False))
        try:
            async with database.session_factory() as session:
                repository = SqlAlchemyMemoryRepository(session)
                await repository.upsert(
                    MemoryCategory.NOTES,
                    "migration_probe",
                    "The migrated repository is writable.",
                )
                entries = await repository.list_all()
                assert [(entry.key, entry.value) for entry in entries] == [
                    ("migration_probe", "The migrated repository is writable.")
                ]

            concurrent_values = [f"value-{index}" for index in range(8)]
            barrier = asyncio.Barrier(len(concurrent_values))
            session_ids: set[int] = set()

            async def write_concurrently(value: str) -> MemoryEntry:
                async with database.session_factory() as session:
                    session_ids.add(id(session))
                    repository = SqlAlchemyMemoryRepository(session)
                    await barrier.wait()
                    return await repository.upsert(
                        MemoryCategory.PREFERENCES,
                        "concurrent_key",
                        value,
                    )

            outcomes = await asyncio.gather(
                *(write_concurrently(value) for value in concurrent_values),
                return_exceptions=True,
            )
            failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
            assert failures == []
            assert len(session_ids) == len(concurrent_values)
            concurrent_results = [cast(MemoryEntry, outcome) for outcome in outcomes]
            assert {entry.value for entry in concurrent_results} == set(concurrent_values)

            async with database.session_factory() as session:
                repository = SqlAlchemyMemoryRepository(session)
                concurrent_entries = [
                    entry
                    for entry in await repository.list_all()
                    if entry.category is MemoryCategory.PREFERENCES
                    and entry.key == "concurrent_key"
                ]
            assert len(concurrent_entries) == 1
            assert concurrent_entries[0].value in concurrent_values

            async with database.engine.connect() as connection:
                revisions = set(
                    (
                        await connection.execute(text("SELECT version_num FROM alembic_version"))
                    ).scalars()
                )
            migration_scripts = ScriptDirectory.from_config(Config(PROJECT_ROOT / "alembic.ini"))
            assert revisions == set(migration_scripts.get_heads())

            identity = SqlAlchemyDeviceIdentity(
                database.session_factory,
                DeviceCredentialCodec(b"postgres-integration-device-digest-key"),
            )
            pairing = SqlAlchemyDevicePairing(
                database.session_factory,
                identity,
            )
            challenge = await pairing.create(
                frozenset({DeviceCapability.OPEN_APP}),
                timedelta(minutes=5),
            )
            credential = await pairing.claim(
                challenge.id,
                challenge.secret,
                "Migration probe device",
            )
            assert credential is not None

            restarted_identity = SqlAlchemyDeviceIdentity(
                database.session_factory,
                DeviceCredentialCodec(b"postgres-integration-device-digest-key"),
            )
            restarted_registry = SqlAlchemyDeviceRegistry(database.session_factory)
            assert await restarted_identity.authenticate(credential.token) == (credential.device.id)
            assert await restarted_registry.get(credential.device.id) == (credential.device)

            rotated_token = await restarted_identity.issue(credential.device.id)
            assert await restarted_identity.authenticate(credential.token) is None
            assert await restarted_identity.authenticate(rotated_token) == (credential.device.id)
            async with database.session_factory() as session:
                credential_count = await session.scalar(
                    select(func.count()).select_from(DeviceCredentialRecord)
                )
            assert credential_count == 1

            await restarted_identity.revoke(credential.device.id)
            assert await restarted_identity.authenticate(rotated_token) is None
        finally:
            await database.dispose()
    finally:
        try:
            if database_created:
                async with admin_engine.connect() as connection:
                    await connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        finally:
            await admin_engine.dispose()
