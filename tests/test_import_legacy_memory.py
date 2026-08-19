import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.domain.memory import MemoryCategory
from app.infrastructure.database import Database
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository
from scripts.import_legacy_memory import import_data, import_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_import_data_normalizes_supported_memory_shapes(
    fake_memory_repository,
    memory_service,
) -> None:
    imported = await import_data(
        {
            "identity": {
                "age": 42,
                "empty": "  ",
                "missing": None,
            },
            "preferences": {
                "favorite_editor": {"value": "PyCharm"},
                "shell": "PowerShell",
            },
            "notes": ["not a key/value object"],
            "unknown_category": {"ignored": "value"},
        },
        memory_service,
    )

    assert imported == 3
    entries = await fake_memory_repository.list_all()
    assert {(entry.category, entry.key, entry.value) for entry in entries} == {
        (MemoryCategory.IDENTITY, "age", "42"),
        (MemoryCategory.PREFERENCES, "favorite_editor", "PyCharm"),
        (MemoryCategory.PREFERENCES, "shell", "PowerShell"),
    }


@pytest.mark.asyncio
async def test_import_file_writes_to_a_migrated_database(tmp_path: Path) -> None:
    source_path = tmp_path / "memory.json"
    source_path.write_text(
        '{"notes": {"migration_note": "Imported successfully"}}',
        encoding="utf-8",
    )
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'import.db').as_posix()}"
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, migration_config, "head")

    assert await import_file(source_path, database_url) == 1

    database = Database(database_url)
    try:
        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            entries = await repository.list_all()
        assert [(entry.category, entry.key, entry.value) for entry in entries] == [
            (
                MemoryCategory.NOTES,
                "migration_note",
                "Imported successfully",
            )
        ]
    finally:
        await database.dispose()
