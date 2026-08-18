from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def migration_config(database_url: str) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def test_initial_migration_upgrades_an_empty_database_and_is_reversible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration.db"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    sync_url = f"sqlite:///{database_path.as_posix()}"
    config = migration_config(async_url)
    monkeypatch.chdir(tmp_path)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(sync_url)
    try:
        schema = inspect(engine)
        assert {"alembic_version", "memories"} <= set(schema.get_table_names())
        assert [column["name"] for column in schema.get_columns("memories")] == [
            "id",
            "category",
            "key",
            "value",
            "updated_at",
        ]
        assert {item["name"] for item in schema.get_indexes("memories")} == {
            "ix_memories_category"
        }
        assert {
            item["name"] for item in schema.get_unique_constraints("memories")
        } == {"uq_memory_category_key"}

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0001_create_memories"
    finally:
        engine.dispose()

    command.check(config)
    command.downgrade(config, "base")

    engine = create_engine(sync_url)
    try:
        assert "memories" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
