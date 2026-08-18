"""Import Mark-L memory/long_term.json into JARVIS Core PostgreSQL.

Usage:
    python -m alembic upgrade head
    python scripts/import_legacy_memory.py /path/to/Mark-L-main/memory/long_term.json
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domain.memory import MemoryCategory, MemoryRepository
from app.infrastructure.database import Database
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository


async def import_data(
    data: dict[str, Any],
    repository: MemoryRepository,
) -> int:
    imported = 0
    for category in MemoryCategory:
        values = data.get(category.value, {})
        if not isinstance(values, dict):
            continue
        for key, raw in values.items():
            if isinstance(raw, dict):
                value = raw.get("value")
            else:
                value = raw
            if value is None or not str(value).strip():
                continue
            await repository.upsert(category, str(key), str(value))
            imported += 1
    return imported


async def import_file(
    path: Path,
    database_url: str | None = None,
) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Memory export must contain a JSON object at its root")

    database = Database(database_url or get_settings().database_url)

    try:
        async with database.session_factory() as session:
            repository = SqlAlchemyMemoryRepository(session)
            return await import_data(data, repository)
    finally:
        await database.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/import_legacy_memory.py /path/to/long_term.json")
    source_path = Path(sys.argv[1])
    imported_count = asyncio.run(import_file(source_path))
    print(f"Imported {imported_count} memories from {source_path}")
