import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.infrastructure.database import Database
from app.infrastructure.repositories.memory import MemoryRecord
from scripts.adopt_alembic_baseline import (
    BASELINE_REVISION,
    PROJECT_ROOT,
    BaselineAdoptionError,
    adopt_baseline,
)


@pytest.mark.asyncio
async def test_adopting_matching_schema_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    database = Database(database_url)
    try:
        async with database.engine.begin() as connection:
            # Reproduce exactly the pre-Alembic schema. Using the global Base
            # metadata here would also create tables introduced after 0001 as
            # soon as their ORM modules are imported during test collection.
            await connection.run_sync(MemoryRecord.__table__.create)
            await connection.execute(
                text(
                    "INSERT INTO memories "
                    "(category, key, value, updated_at) "
                    "VALUES (:category, :key, :value, :updated_at)"
                ),
                {
                    "category": "preferences",
                    "key": "favorite_editor",
                    "value": "PyCharm",
                    "updated_at": "2026-08-18 12:00:00+00:00",
                },
            )
    finally:
        await database.dispose()

    await adopt_baseline(database_url)

    database = Database(database_url)
    try:
        async with database.engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT value FROM memories WHERE key = 'favorite_editor'")
                )
                == "PyCharm"
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == BASELINE_REVISION
            )
    finally:
        await database.dispose()

    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, migration_config, "head")

    database = Database(database_url)
    try:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            assert {"memories", "devices", "device_credentials"} <= tables
            assert (
                await connection.scalar(
                    text("SELECT value FROM memories WHERE key = 'favorite_editor'")
                )
                == "PyCharm"
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_persist_devices"
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_adoption_refuses_an_empty_database(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"

    with pytest.raises(BaselineAdoptionError, match="upgrade head"):
        await adopt_baseline(database_url)


@pytest.mark.asyncio
async def test_adoption_refuses_to_stamp_a_different_schema(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'different.db').as_posix()}"
    database = Database(database_url)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE memories (id INTEGER PRIMARY KEY)"))
    finally:
        await database.dispose()

    with pytest.raises(BaselineAdoptionError, match="Differences"):
        await adopt_baseline(database_url)

    database = Database(database_url)
    try:
        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        assert "alembic_version" not in table_names
    finally:
        await database.dispose()
