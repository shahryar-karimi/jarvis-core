import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.domain.memory import MemoryCategory
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

            entries = await repository.list_all()
            assert created.key == "favorite_editor"
            assert updated.value == "PyCharm"
            assert entries[0].category is MemoryCategory.NOTES
            assert {
                (entry.category, entry.key, entry.value) for entry in entries
            } == {
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
            assert await repository.delete(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
            ) is True
            assert await repository.delete(
                MemoryCategory.PREFERENCES,
                "favorite_editor",
            ) is False
            remaining = await repository.list_all()
            assert [entry.category for entry in remaining] == [MemoryCategory.NOTES]
    finally:
        await database.dispose()
