"""Opt-in integration tests against the PostgreSQL configured in .env.

Run with:
    Set RUN_DB_TESTS=1 in .env, then run:
    python -m pytest -m integration tests/test_database_integration.py -v

PowerShell:
    $env:RUN_DB_TESTS = "1"
    python -m pytest -m integration tests/test_database_integration.py -v
"""

import pytest
from sqlalchemy import text

from app.infrastructure.database import create_schema, engine


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_connection_and_schema_creation(
    external_test_settings,
) -> None:
    if not external_test_settings.run_db_tests:
        pytest.skip(
            "Set RUN_DB_TESTS=1 in .env to test the configured PostgreSQL database"
        )

    try:
        await create_schema()

        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
