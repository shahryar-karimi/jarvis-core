import asyncio
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event

from app.domain.memory import MemoryCategory, MemoryEntry
from app.infrastructure.database import Database
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_repository_round_trip_against_migrated_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "repository.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, migration_config, "head")

    database = Database(database_url)
    try:
        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            created = await repository.upsert(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
                "VS Code",
            )
            updated = await repository.upsert(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
                "PyCharm",
            )
            await repository.upsert(
                MemoryCategory.NOTES,
                "favorite_editor",
                "This key is isolated by category.",
            )

        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            entries = await repository.list_all()
            assert created.key == "favorite_editor"
            assert updated.value == "PyCharm"
            assert entries[0].category is MemoryCategory.NOTES
            assert {(entry.category, entry.key, entry.value) for entry in entries} == {
                (
                    MemoryCategory.PREFERENCES,
                    "favorite_editor",
                    "PyCharm",
                ),
                (
                    MemoryCategory.NOTES,
                    "favorite_editor",
                    "This key is isolated by category.",
                ),
            }
            assert (
                await repository.delete(
                    MemoryCategory.PREFERENCES,
                    "favorite_editor",
                )
                is True
            )

        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            assert (
                await repository.delete(
                    MemoryCategory.PREFERENCES,
                    "favorite_editor",
                )
                is False
            )
            remaining = await repository.list_all()
            assert [entry.category for entry in remaining] == [MemoryCategory.NOTES]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_upserts_keep_one_row_per_category_and_key(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-repository.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, migration_config, "head")

    database = Database(database_url)
    values = [f"editor-{index}" for index in range(8)]
    barrier = asyncio.Barrier(len(values))
    session_ids: set[int] = set()
    executed_statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        executed_statements.append(statement)

    event.listen(
        database.engine.sync_engine,
        "before_cursor_execute",
        capture_statement,
    )

    async def write(value: str) -> MemoryEntry:
        async with database.session_factory() as session:
            session_ids.add(id(session))
            repository = SqlAlchemyMemoryRepository(session)
            await barrier.wait()
            return await repository.upsert(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
                value,
            )

    try:
        outcomes = await asyncio.gather(
            *(write(value) for value in values),
            return_exceptions=True,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failures == []
        assert len(session_ids) == len(values)
        results = [cast(MemoryEntry, outcome) for outcome in outcomes]

        assert {entry.value for entry in results} == set(values)
        memory_statements = [
            statement.upper()
            for statement in executed_statements
            if "MEMORIES" in statement.upper()
        ]
        assert len(memory_statements) == len(values)
        assert all(
            statement.lstrip().startswith("INSERT") and "ON CONFLICT" in statement
            for statement in memory_statements
        )

        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            entries = await repository.list_all()

        assert len(entries) == 1
        assert entries[0].category is MemoryCategory.PREFERENCES
        assert entries[0].key == "favorite_editor"
        assert entries[0].value in values
    finally:
        event.remove(
            database.engine.sync_engine,
            "before_cursor_execute",
            capture_statement,
        )
        await database.dispose()
