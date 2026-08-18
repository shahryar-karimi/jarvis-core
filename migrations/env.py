from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, URL
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.infrastructure.database import Base
from app.infrastructure.repositories.memory import MemoryRecord  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str | URL:
    """Resolve a test override first, then the application's configured URL."""
    override = config.attributes.get("database_url")
    if override is not None:
        if not isinstance(override, (str, URL)):
            raise TypeError("Alembic database_url override must be a string or URL")
        return override
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run Alembic's synchronous migration work through an async engine."""
    connectable = create_async_engine(
        get_database_url(),
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
