from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.devices import DeviceService
from app.core.config import Settings
from app.domain.devices import CapabilityRegistry, DeviceConnectionManager, DeviceRegistry
from app.domain.security import DeviceIdentity, DevicePairing
from app.infrastructure.devices import (
    InMemoryCapabilityRegistry,
    InMemoryDeviceConnectionManager,
    InMemoryDeviceRegistry,
)
from app.infrastructure.repositories import SqlAlchemyDeviceRegistry
from app.infrastructure.security import (
    DeviceCredentialCodec,
    InMemoryDeviceIdentity,
    InMemoryDevicePairing,
    SqlAlchemyDeviceIdentity,
    SqlAlchemyDevicePairing,
)


SessionFactory = async_sessionmaker[AsyncSession]
_PLACEHOLDER_DIGEST_KEYS = {
    "change-me",
    "replace-me",
    "replace-with-a-random-secret",
}


@dataclass(frozen=True, slots=True)
class HiveResources:
    device_service: DeviceService
    device_registry: DeviceRegistry
    device_identity: DeviceIdentity
    device_pairing: DevicePairing
    device_connections: DeviceConnectionManager
    capability_registry: CapabilityRegistry

    async def aclose(self) -> None:
        await self.device_service.shutdown()


def create_hive_resources(
    settings: Settings,
    session_factory: SessionFactory,
) -> HiveResources:
    """Compose durable device identity with process-local live routing."""

    digest_key = _credential_digest_key(settings)
    registry = SqlAlchemyDeviceRegistry(session_factory)
    identity = SqlAlchemyDeviceIdentity(
        session_factory,
        DeviceCredentialCodec(digest_key),
    )
    pairing = SqlAlchemyDevicePairing(session_factory, identity)
    connections = InMemoryDeviceConnectionManager()
    capabilities = InMemoryCapabilityRegistry()
    service = DeviceService(
        registry,
        identity,
        pairing,
        connections,
        capabilities,
        pairing_ttl_seconds=settings.device_pairing_ttl_seconds,
        command_timeout_seconds=settings.device_command_timeout_seconds,
    )
    return HiveResources(
        device_service=service,
        device_registry=registry,
        device_identity=identity,
        device_pairing=pairing,
        device_connections=connections,
        capability_registry=capabilities,
    )


def create_in_memory_hive_resources(
    settings: Settings,
    _session_factory: object | None = None,
) -> HiveResources:
    """Compose isolated adapters for tests that intentionally have no SQL DB."""

    registry = InMemoryDeviceRegistry()
    identity = InMemoryDeviceIdentity()
    pairing = InMemoryDevicePairing(registry, identity)
    connections = InMemoryDeviceConnectionManager()
    capabilities = InMemoryCapabilityRegistry()
    service = DeviceService(
        registry,
        identity,
        pairing,
        connections,
        capabilities,
        pairing_ttl_seconds=settings.device_pairing_ttl_seconds,
        command_timeout_seconds=settings.device_command_timeout_seconds,
    )
    return HiveResources(
        device_service=service,
        device_registry=registry,
        device_identity=identity,
        device_pairing=pairing,
        device_connections=connections,
        capability_registry=capabilities,
    )


def _credential_digest_key(settings: Settings) -> bytes:
    configured = settings.device_credential_digest_key
    raw_value = configured.get_secret_value().strip() if configured else ""
    encoded = raw_value.encode("utf-8")
    if (
        len(encoded) < 32
        or raw_value.casefold() in _PLACEHOLDER_DIGEST_KEYS
    ):
        raise RuntimeError(
            "Device credential persistence requires "
            "JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY with at least 32 bytes."
        )
    return encoded
