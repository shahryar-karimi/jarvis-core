from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.device_dependencies import (
    CapabilityRegistryDep,
    DeviceAdminDep,
    DeviceServiceDep,
)
from app.api.device_schemas import (
    CapabilityResponse,
    DeviceCommandRequest,
    DeviceCommandResponse,
    DeviceResponse,
    PairDeviceRequest,
    PairDeviceResponse,
    PairingCreateRequest,
    PairingResponse,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/pairings",
    response_model=PairingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pairing(
    request: PairingCreateRequest,
    service: DeviceServiceDep,
    _: DeviceAdminDep,
    response: Response,
) -> PairingResponse:
    challenge = await service.create_pairing(frozenset(request.granted_capabilities))
    response.headers["Cache-Control"] = "no-store"
    return PairingResponse(
        pairing_id=challenge.id,
        pairing_secret=challenge.secret,
        granted_capabilities=sorted(challenge.granted_capabilities),
        expires_at=challenge.expires_at,
    )


@router.post(
    "/pair",
    response_model=PairDeviceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pair_device(
    request: PairDeviceRequest,
    service: DeviceServiceDep,
    response: Response,
) -> PairDeviceResponse:
    credential = await service.pair_device(
        request.pairing_id,
        request.pairing_secret,
        request.device_name,
    )
    response.headers["Cache-Control"] = "no-store"
    return PairDeviceResponse(
        device=DeviceResponse.from_domain(credential.device),
        device_token=credential.token,
    )


@router.get("/capabilities", response_model=list[CapabilityResponse])
async def list_capabilities(
    registry: CapabilityRegistryDep,
    _: DeviceAdminDep,
) -> list[CapabilityResponse]:
    return [CapabilityResponse.from_domain(definition) for definition in await registry.list_all()]


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    service: DeviceServiceDep,
    _: DeviceAdminDep,
) -> list[DeviceResponse]:
    return [DeviceResponse.from_domain(device) for device in await service.list_devices()]


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    service: DeviceServiceDep,
    _: DeviceAdminDep,
) -> DeviceResponse:
    return DeviceResponse.from_domain(await service.get_device(device_id))


@router.delete("/{device_id}", response_model=DeviceResponse)
async def revoke_device(
    device_id: UUID,
    service: DeviceServiceDep,
    _: DeviceAdminDep,
) -> DeviceResponse:
    return DeviceResponse.from_domain(await service.revoke_device(device_id))


@router.post(
    "/{device_id}/commands",
    response_model=DeviceCommandResponse,
)
async def execute_command(
    device_id: UUID,
    request: DeviceCommandRequest,
    service: DeviceServiceDep,
    _: DeviceAdminDep,
) -> DeviceCommandResponse:
    command, result = await service.execute_command(
        device_id,
        request.capability,
        request.action,
        request.arguments,
    )
    return DeviceCommandResponse.from_domain(command, result)
