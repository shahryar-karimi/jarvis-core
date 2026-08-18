"""Import Mark-L memory/long_term.json into JARVIS Core PostgreSQL.

Usage:
    python scripts/import_legacy_memory.py /path/to/Mark-L-main/memory/long_term.json
"""

import asyncio
import json
import sys
from pathlib import Path

from app.domain.memory import MemoryCategory
from app.infrastructure.database import SessionFactory, create_schema
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository


async def import_file(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    await create_schema()

    async with SessionFactory() as session:
        repository = SqlAlchemyMemoryRepository(session)
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

    print(f"Imported {imported} memories from {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/import_legacy_memory.py /path/to/long_term.json")
    asyncio.run(import_file(Path(sys.argv[1])))
