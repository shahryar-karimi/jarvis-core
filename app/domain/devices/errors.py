from uuid import UUID

from app.domain.devices.models import DeviceCapability


class DeviceError(Exception):
    """Base error for framework-neutral device behavior."""


class DeviceNotFoundError(DeviceError, LookupError):
    def __init__(self, device_id: UUID) -> None:
        self.device_id = device_id
        super().__init__(f"Device '{device_id}' was not found.")


class DeviceAuthenticationError(DeviceError):
    def __init__(self) -> None:
        super().__init__("Device authentication failed.")


class InvalidPairingError(DeviceError):
    def __init__(self) -> None:
        super().__init__("The pairing request is invalid or has expired.")


class DeviceRevokedError(DeviceError):
    def __init__(self, device_id: UUID) -> None:
        self.device_id = device_id
        super().__init__(f"Device '{device_id}' has been revoked.")


class DeviceOfflineError(DeviceError):
    def __init__(self, device_id: UUID) -> None:
        self.device_id = device_id
        super().__init__(f"Device '{device_id}' is offline.")


class DeviceCapabilityUnavailableError(DeviceError):
    def __init__(self, device_id: UUID, capability: DeviceCapability) -> None:
        self.device_id = device_id
        self.capability = capability
        super().__init__(
            f"Device '{device_id}' cannot execute capability "
            f"'{capability.value}'."
        )


class DeviceActionUnavailableError(DeviceError):
    def __init__(self, capability: DeviceCapability, action: str) -> None:
        self.capability = capability
        self.action = action
        super().__init__(
            f"Action '{action}' is not registered for capability "
            f"'{capability.value}'."
        )


class DeviceCommandTimeoutError(DeviceError, TimeoutError):
    def __init__(self, command_id: UUID) -> None:
        self.command_id = command_id
        super().__init__(f"Device command '{command_id}' timed out.")


class CommandResultRejectedError(DeviceError):
    def __init__(self, command_id: UUID) -> None:
        self.command_id = command_id
        super().__init__(f"Command result '{command_id}' was not expected.")
