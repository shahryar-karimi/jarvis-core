from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MemoryCategory(StrEnum):
    IDENTITY = "identity"
    PREFERENCES = "preferences"
    PROJECTS = "projects"
    RELATIONSHIPS = "relationships"
    WISHES = "wishes"
    NOTES = "notes"


@dataclass(slots=True)
class MemoryEntry:
    category: MemoryCategory
    key: str
    value: str
    updated_at: datetime


class MemoryRepository(ABC):
    @abstractmethod
    async def list_all(self) -> list[MemoryEntry]:
        raise NotImplementedError

    @abstractmethod
    async def upsert(self, category: MemoryCategory, key: str, value: str) -> MemoryEntry:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, category: MemoryCategory, key: str) -> bool:
        raise NotImplementedError
