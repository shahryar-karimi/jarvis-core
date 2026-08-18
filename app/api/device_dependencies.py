import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.devices import DeviceService
from app.core.config import Settings
from app.domain.devices import CapabilityRegistry
from app.infrastructure.hive import HiveResources


_admin_bearer = HTTPBearer(auto_error=False)
_PLACEHOLDER_ADMIN_TOKENS = {
    "change-me",
    "replace-me",
    "replace-with-a-random-secret",
}


def get_hive_resources(request: Request) -> HiveResources:
    try:
        return cast(HiveResources, request.app.state.hive)
    except AttributeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device gateway resources are not initialized.",
        ) from error


def get_device_service(
    resources: Annotated[HiveResources, Depends(get_hive_resources)],
) -> DeviceService:
    return resources.device_service


def get_capability_registry(
    resources: Annotated[HiveResources, Depends(get_hive_resources)],
) -> CapabilityRegistry:
    return resources.capability_registry


def require_device_admin(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_admin_bearer),
    ],
) -> None:
    settings = cast(Settings, request.app.state.settings)
    configured = settings.device_admin_token
    expected = configured.get_secret_value().strip() if configured else ""
    if (
        len(expected) < 32
        or expected.casefold() in _PLACEHOLDER_ADMIN_TOKENS
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device administration is not configured.",
        )
    supplied = credentials.credentials if credentials else ""
    if (
        credentials is None
        or credentials.scheme.casefold() != "bearer"
        or not secrets.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device administrator credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]
CapabilityRegistryDep = Annotated[
    CapabilityRegistry,
    Depends(get_capability_registry),
]
DeviceAdminDep = Annotated[None, Depends(require_device_admin)]
