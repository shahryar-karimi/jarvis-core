from dataclasses import dataclass

from app.application.devices import DeviceService
from app.core.config import Settings
from app.domain.devices import CapabilityRegistry, DeviceConnectionManager, DeviceRegistry
from app.domain.security import DeviceIdentity, DevicePairing
from app.infrastructure.devices import (
    InMemoryCapabilityRegistry,
    InMemoryDeviceConnectionManager,
    InMemoryDeviceRegistry,
)
from app.infrastructure.security import InMemoryDeviceIdentity, InMemoryDevicePairing


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


def create_hive_resources(settings: Settings) -> HiveResources:
    """Compose the single-process gateway used by the initial Core release."""

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
