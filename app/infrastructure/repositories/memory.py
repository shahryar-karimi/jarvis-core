from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
        statement = select(MemoryRecord).order_by(MemoryRecord.updated_at.desc())
        records = (await self._session.scalars(statement)).all()
        return [self._to_domain(record) for record in records]

    async def upsert(
        self,
        category: MemoryCategory,
        key: str,
        value: str,
    ) -> MemoryEntry:
        """Insert or update one composite key in a single database statement."""

        now = datetime.now(UTC)
        values = {
            "category": category.value,
            "key": key,
            "value": value,
            "updated_at": now,
        }
        dialect_name = self._session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert_statement = postgresql_insert(MemoryRecord).values(**values)
        elif dialect_name == "sqlite":
            insert_statement = sqlite_insert(MemoryRecord).values(**values)
        else:
            raise RuntimeError(f"Atomic memory upsert is not implemented for '{dialect_name}'.")

        upsert_statement = insert_statement.on_conflict_do_update(
            index_elements=[MemoryRecord.category, MemoryRecord.key],
            set_={
                "value": insert_statement.excluded.value,
                "updated_at": insert_statement.excluded.updated_at,
            },
        ).returning(MemoryRecord)
        record = (
            await self._session.scalars(
                upsert_statement,
                # A conflicting row may already be present in this identity map.
                execution_options={"populate_existing": True},
            )
        ).one()
        entry = self._to_domain(record)

        await self._session.commit()
        return entry

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
