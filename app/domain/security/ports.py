from abc import ABC, abstractmethod
from datetime import timedelta
from uuid import UUID

from app.domain.devices import DeviceCapability
from app.domain.security.models import DeviceCredential, DevicePairingChallenge


class DeviceIdentity(ABC):
    @abstractmethod
    async def issue(self, device_id: UUID) -> str:
        raise NotImplementedError

    @abstractmethod
    async def authenticate(self, token: str) -> UUID | None:
        raise NotImplementedError

    @abstractmethod
    async def revoke(self, device_id: UUID) -> None:
        raise NotImplementedError


class DevicePairing(ABC):
    @abstractmethod
    async def create(
        self,
        granted_capabilities: frozenset[DeviceCapability],
        ttl: timedelta,
    ) -> DevicePairingChallenge:
        raise NotImplementedError

    @abstractmethod
    async def claim(
        self,
        pairing_id: UUID,
        secret: str,
        device_name: str,
    ) -> DeviceCredential | None:
        """Atomically consume a challenge and register its device identity."""

        raise NotImplementedError
