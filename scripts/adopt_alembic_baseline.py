"""Safely adopt Alembic for a database containing the original schema.

Usage:
    python scripts/adopt_alembic_baseline.py
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
)

from app.core.config import get_settings
from app.infrastructure.database import Database


BASELINE_REVISION = "0001_create_memories"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_METADATA = MetaData()
BASELINE_MEMORIES = Table(
    "memories",
    BASELINE_METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category", String(32), nullable=False),
    Column("key", String(120), nullable=False),
    Column("value", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("category", "key", name="uq_memory_category_key"),
)
Index("ix_memories_category", BASELINE_MEMORIES.c.category)


class BaselineAdoptionError(RuntimeError):
    """Raised when stamping could hide a real schema change."""


async def adopt_baseline(database_url: str | None = None) -> None:
    resolved_url = database_url or get_settings().database_url
    database = Database(resolved_url)
    differences = []

    try:
        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(
                    inspect(sync_connection).get_table_names()
                )
            )
            if "alembic_version" in table_names:
                raise BaselineAdoptionError(
                    "This database is already managed by Alembic; use "
                    "'python -m alembic current --check-heads'."
                )
            if "memories" not in table_names:
                raise BaselineAdoptionError(
                    "The memories table is absent; use "
                    "'python -m alembic upgrade head' instead."
                )

            def schema_differences(sync_connection):
                migration_context = MigrationContext.configure(
                    sync_connection,
                    opts={
                        "compare_type": True,
                        "compare_server_default": True,
                    },
                )
                return compare_metadata(
                    migration_context,
                    BASELINE_METADATA,
                )

            differences = await connection.run_sync(schema_differences)
    finally:
        await database.dispose()

    if differences:
        difference_summary = "\n".join(f"- {item!r}" for item in differences)
        raise BaselineAdoptionError(
            "The database does not exactly match the Alembic baseline; no "
            f"revision was stamped. Differences:\n{difference_summary}"
        )

    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = resolved_url
    await asyncio.to_thread(
        command.stamp,
        migration_config,
        BASELINE_REVISION,
    )


if __name__ == "__main__":
    asyncio.run(adopt_baseline())
    print(f"Database adopted at Alembic revision {BASELINE_REVISION}.")
