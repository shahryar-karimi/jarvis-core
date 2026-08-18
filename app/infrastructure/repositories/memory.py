from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, UniqueConstraint, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.memory import MemoryCategory, MemoryEntry, MemoryRepository
from app.infrastructure.database import Base


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("category", "key", name="uq_memory_category_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyMemoryRepository(MemoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[MemoryEntry]:
        records = (await self._session.scalars(select(MemoryRecord).order_by(MemoryRecord.updated_at.desc()))).all()
        return [self._to_domain(record) for record in records]

    async def upsert(self, category: MemoryCategory, key: str, value: str) -> MemoryEntry:
        record = await self._session.scalar(
            select(MemoryRecord).where(
                MemoryRecord.category == category.value,
                MemoryRecord.key == key,
            )
        )
        now = datetime.now(timezone.utc)
        if record is None:
            record = MemoryRecord(category=category.value, key=key, value=value, updated_at=now)
            self._session.add(record)
        else:
            record.value = value
            record.updated_at = now

        await self._session.commit()
        await self._session.refresh(record)
        return self._to_domain(record)

    async def delete(self, category: MemoryCategory, key: str) -> bool:
        result = await self._session.execute(
            delete(MemoryRecord).where(
                MemoryRecord.category == category.value,
                MemoryRecord.key == key,
            )
        )
        await self._session.commit()
        return bool(result.rowcount)

    @staticmethod
    def _to_domain(record: MemoryRecord) -> MemoryEntry:
        return MemoryEntry(
            category=MemoryCategory(record.category),
            key=record.key,
            value=record.value,
            updated_at=record.updated_at,
        )
