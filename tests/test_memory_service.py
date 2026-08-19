from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.application.memory_service import MemoryNotFoundError, MemoryService
from app.domain.memory import MemoryCategory, MemoryEntry, MemoryRepository


def memory_entry() -> MemoryEntry:
    return MemoryEntry(
        category=MemoryCategory.PREFERENCES,
        key="favorite_editor",
        value="PyCharm",
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_memory_service_delegates_reads_and_writes() -> None:
    repository = AsyncMock(spec=MemoryRepository)
    entry = memory_entry()
    repository.list_all.return_value = [entry]
    repository.upsert.return_value = entry
    service = MemoryService(repository)

    assert await service.list_memories() == [entry]
    assert (
        await service.upsert_memory(
            MemoryCategory.PREFERENCES,
            "favorite_editor",
            "PyCharm",
        )
        is entry
    )

    repository.list_all.assert_awaited_once_with()
    repository.upsert.assert_awaited_once_with(
        MemoryCategory.PREFERENCES,
        "favorite_editor",
        "PyCharm",
    )


@pytest.mark.asyncio
async def test_memory_service_deletes_an_existing_memory() -> None:
    repository = AsyncMock(spec=MemoryRepository)
    repository.delete.return_value = True
    service = MemoryService(repository)

    await service.delete_memory(
        MemoryCategory.PREFERENCES,
        "favorite_editor",
    )

    repository.delete.assert_awaited_once_with(
        MemoryCategory.PREFERENCES,
        "favorite_editor",
    )


@pytest.mark.asyncio
async def test_memory_service_reports_a_missing_memory() -> None:
    repository = AsyncMock(spec=MemoryRepository)
    repository.delete.return_value = False
    service = MemoryService(repository)

    with pytest.raises(MemoryNotFoundError) as captured:
        await service.delete_memory(
            MemoryCategory.PREFERENCES,
            "favorite_editor",
        )

    assert captured.value.category is MemoryCategory.PREFERENCES
    assert captured.value.key == "favorite_editor"
    assert str(captured.value) == ("Memory 'preferences/favorite_editor' was not found.")


@pytest.mark.asyncio
async def test_memory_service_does_not_hide_repository_errors() -> None:
    repository = AsyncMock(spec=MemoryRepository)
    failure = RuntimeError("database unavailable")
    repository.list_all.side_effect = failure
    service = MemoryService(repository)

    with pytest.raises(RuntimeError) as captured:
        await service.list_memories()

    assert captured.value is failure
