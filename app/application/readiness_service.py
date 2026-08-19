from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DatabaseReadiness:
    """Result of checking the database and its managed schema."""

    available: bool
    migrations_current: bool | None


class ReadinessProbe(Protocol):
    async def check(self) -> DatabaseReadiness:
        """Check dependencies needed before this process can serve traffic."""
        ...


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    database: str
    migrations: str


class ReadinessService:
    """Translate dependency checks into a stable, non-sensitive API report."""

    def __init__(self, probe: ReadinessProbe) -> None:
        self._probe = probe

    async def check(self) -> ReadinessReport:
        try:
            result = await self._probe.check()
        except Exception:
            # A readiness endpoint must fail closed without exposing driver,
            # host, database, or migration details to an unauthenticated caller.
            logger.error("Unexpected readiness probe failure")
            result = DatabaseReadiness(
                available=False,
                migrations_current=None,
            )

        database = "ok" if result.available else "unavailable"
        if result.migrations_current is True:
            migrations = "current"
        elif result.migrations_current is False:
            migrations = "outdated"
        else:
            migrations = "unknown"

        return ReadinessReport(
            ready=result.available and result.migrations_current is True,
            database=database,
            migrations=migrations,
        )
