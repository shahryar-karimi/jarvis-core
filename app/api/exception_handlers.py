from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.application.memory_service import MemoryNotFoundError
from app.domain.devices import (
    DeviceActionUnavailableError,
    DeviceAuthenticationError,
    DeviceCapabilityUnavailableError,
    DeviceCommandTimeoutError,
    DeviceError,
    DeviceNotFoundError,
    DeviceOfflineError,
    DeviceRevokedError,
    InvalidPairingError,
)


async def memory_not_found_handler(
    _: Request,
    error: MemoryNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(error)},
    )


async def device_error_handler(_: Request, error: DeviceError) -> JSONResponse:
    if isinstance(error, DeviceNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, (DeviceAuthenticationError, InvalidPairingError)):
        status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(error, DeviceRevokedError):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, DeviceCommandTimeoutError):
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
    elif isinstance(
        error,
        (
            DeviceOfflineError,
            DeviceCapabilityUnavailableError,
            DeviceActionUnavailableError,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return JSONResponse(
        status_code=status_code,
        content={"detail": str(error)},
    )
