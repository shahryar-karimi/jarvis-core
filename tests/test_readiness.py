from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from app.application.readiness_service import (
    DatabaseReadiness,
    ReadinessService,
)
from app.infrastructure.database import Database
from app.infrastructure.readiness import SqlAlchemyReadinessProbe


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StubProbe:
    def __init__(self, result: DatabaseReadiness) -> None:
        self._result = result

    async def check(self) -> DatabaseReadiness:
        return self._result


class FailingProbe:
    async def check(self) -> DatabaseReadiness:
        raise RuntimeError("postgresql://private-user:private-password@db/private")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_result", "expected"),
    [
        (
            DatabaseReadiness(available=True, migrations_current=True),
            (True, "ok", "current"),
        ),
        (
            DatabaseReadiness(available=True, migrations_current=False),
            (False, "ok", "outdated"),
        ),
        (
            DatabaseReadiness(available=True, migrations_current=None),
            (False, "ok", "unknown"),
        ),
        (
            DatabaseReadiness(available=False, migrations_current=None),
            (False, "unavailable", "unknown"),
        ),
    ],
)
async def test_readiness_service_maps_probe_results(
    probe_result: DatabaseReadiness,
    expected: tuple[bool, str, str],
) -> None:
    report = await ReadinessService(StubProbe(probe_result)).check()

    assert (report.ready, report.database, report.migrations) == expected


@pytest.mark.asyncio
async def test_readiness_service_fails_closed_without_logging_secrets(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_logger = logging.getLogger("app.application.readiness_service")
    monkeypatch.setattr(service_logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=service_logger.name)

    report = await ReadinessService(FailingProbe()).check()

    assert report.ready is False
    assert report.database == "unavailable"
    assert report.migrations == "unknown"
    assert "private-user" not in caplog.text
    assert "private-password" not in caplog.text
    assert "Unexpected readiness probe failure" in caplog.text


@pytest.mark.asyncio
async def test_sqlalchemy_probe_checks_database_and_alembic_head(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'ready.db').as_posix()}"
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    database = Database(database_url)
    probe = SqlAlchemyReadinessProbe(database.engine)

    try:
        await asyncio.to_thread(command.upgrade, migration_config, "head")
        ready_result = await probe.check()

        await asyncio.to_thread(
            command.downgrade,
            migration_config,
            "0001_create_memories",
        )
        outdated_result = await probe.check()
    finally:
        await database.dispose()

    assert ready_result == DatabaseReadiness(
        available=True,
        migrations_current=True,
    )
    assert outdated_result == DatabaseReadiness(
        available=True,
        migrations_current=False,
    )


@pytest.mark.asyncio
async def test_sqlalchemy_probe_reports_unavailable_database(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_parent = tmp_path / "private-password" / "database.db"
    database = Database(f"sqlite+aiosqlite:///{missing_parent.as_posix()}")
    probe_logger = logging.getLogger("app.infrastructure.readiness")
    monkeypatch.setattr(probe_logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=probe_logger.name)

    try:
        result = await SqlAlchemyReadinessProbe(database.engine).check()
    finally:
        await database.dispose()

    assert result == DatabaseReadiness(
        available=False,
        migrations_current=None,
    )
    assert "private-password" not in caplog.text
    assert "Database readiness check failed" in caplog.text


@pytest.mark.asyncio
async def test_sqlalchemy_probe_has_an_application_side_deadline(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = SqlAlchemyReadinessProbe(
        object(),  # type: ignore[arg-type]
        timeout_seconds=0.01,
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_check() -> DatabaseReadiness:
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
        return DatabaseReadiness(available=True, migrations_current=True)

    monkeypatch.setattr(probe, "_check_once", slow_check)
    probe_logger = logging.getLogger("app.infrastructure.readiness")
    monkeypatch.setattr(probe_logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=probe_logger.name)

    result = await probe.check()

    assert result == DatabaseReadiness(
        available=False,
        migrations_current=None,
    )
    assert started.is_set()
    assert cancelled.is_set()
    assert "Database readiness check timed out" in caplog.text
