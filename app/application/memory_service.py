from app.domain.memory import MemoryCategory, MemoryEntry, MemoryRepository


class MemoryNotFoundError(LookupError):
    def __init__(self, category: MemoryCategory, key: str) -> None:
        self.category = category
        self.key = key
        super().__init__(f"Memory '{category.value}/{key}' was not found.")


class MemoryService:
    """Application use cases for long-term memory management."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def list_memories(self) -> list[MemoryEntry]:
        return await self._repository.list_all()

    async def upsert_memory(
        self,
        category: MemoryCategory,
        key: str,
        value: str,
    ) -> MemoryEntry:
        return await self._repository.upsert(category, key, value)

    async def delete_memory(
        self,
        category: MemoryCategory,
        key: str,
    ) -> None:
        deleted = await self._repository.delete(category, key)
        if not deleted:
            raise MemoryNotFoundError(category, key)
