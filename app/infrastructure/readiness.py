from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.readiness_service import (
    DatabaseReadiness,
    ReadinessProbe,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SqlAlchemyReadinessProbe(ReadinessProbe):
    """Verify database connectivity and the deployed Alembic schema head."""

    def __init__(
        self,
        engine: AsyncEngine,
        alembic_config_path: Path | None = None,
        *,
        timeout_seconds: float = 4.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Readiness timeout must be positive")
        self._engine = engine
        self._alembic_config_path = alembic_config_path or PROJECT_ROOT / "alembic.ini"
        self._timeout_seconds = timeout_seconds

    async def check(self) -> DatabaseReadiness:
        try:
            return await asyncio.wait_for(
                self._check_once(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            logger.error("Database readiness check timed out")
            return DatabaseReadiness(
                available=False,
                migrations_current=None,
            )

    async def _check_once(self) -> DatabaseReadiness:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

                try:
                    current_heads = set(await connection.run_sync(_current_migration_heads))
                    expected_heads = self._expected_migration_heads()
                except Exception:
                    logger.error("Unable to inspect database migration state")
                    return DatabaseReadiness(
                        available=True,
                        migrations_current=None,
                    )
        except Exception:
            logger.error("Database readiness check failed")
            return DatabaseReadiness(
                available=False,
                migrations_current=None,
            )

        return DatabaseReadiness(
            available=True,
            migrations_current=current_heads == expected_heads,
        )

    def _expected_migration_heads(self) -> set[str]:
        config = Config(str(self._alembic_config_path))
        scripts = ScriptDirectory.from_config(config)
        return set(scripts.get_heads())


def _current_migration_heads(connection: Connection) -> tuple[str, ...]:
    return MigrationContext.configure(connection).get_current_heads()
