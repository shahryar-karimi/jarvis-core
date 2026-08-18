from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    delete,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.devices import (
    Device,
    DeviceCapability,
    DeviceNotFoundError,
    DeviceRegistry,
    DeviceStatus,
)
from app.infrastructure.database import Base


SessionFactory = async_sessionmaker[AsyncSession]


class DeviceRecord(Base):
    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    granted_capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    paired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DeviceCredentialRecord(Base):
    __tablename__ = "device_credentials"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            name="uq_device_credentials_device_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    device_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "devices.id",
            name="fk_device_credentials_device_id_devices",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class SqlAlchemyDeviceRegistry(DeviceRegistry):
    """Persist durable registrations while leaving connection state in memory."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def add(self, device: Device) -> None:
        record = self.record_from_domain(device)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(record)
        except IntegrityError as error:
            raise ValueError(
                f"Device '{device.id}' is already registered."
            ) from error

    async def get(self, device_id: UUID) -> Device | None:
        async with self._session_factory() as session:
            record = await session.get(DeviceRecord, device_id)
            return self.to_domain(record) if record is not None else None

    async def list_all(self) -> list[Device]:
        statement = select(DeviceRecord).order_by(
            DeviceRecord.paired_at,
            DeviceRecord.id,
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
            return [self.to_domain(record) for record in records]

    async def save(self, device: Device) -> None:
        values = self.durable_values(device)
        statement = (
            update(DeviceRecord)
            .where(DeviceRecord.id == device.id)
            .values(**values)
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                if result.rowcount == 0:
                    raise DeviceNotFoundError(device.id)

    async def remove(self, device_id: UUID) -> bool:
        statement = delete(DeviceRecord).where(DeviceRecord.id == device_id)
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    @staticmethod
    def durable_values(device: Device) -> dict[str, object]:
        return {
            "name": device.name,
            "granted_capabilities": sorted(
                capability.value for capability in device.granted_capabilities
            ),
            "paired_at": device.paired_at,
            "revoked_at": device.revoked_at,
        }

    @classmethod
    def record_from_domain(cls, device: Device) -> DeviceRecord:
        return DeviceRecord(id=device.id, **cls.durable_values(device))

    @staticmethod
    def to_domain(record: DeviceRecord) -> Device:
        revoked_at = _as_utc(record.revoked_at)
        return Device(
            id=record.id,
            name=record.name,
            granted_capabilities=frozenset(
                DeviceCapability(value)
                for value in record.granted_capabilities
            ),
            paired_at=_as_utc(record.paired_at),
            status=(
                DeviceStatus.REVOKED
                if revoked_at is not None
                else DeviceStatus.OFFLINE
            ),
            revoked_at=revoked_at,
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
