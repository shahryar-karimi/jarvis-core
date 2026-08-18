from fastapi import APIRouter, Depends

from app.api.owner_dependencies import require_owner
from app.api.routes import chat, devices, health, memories
from app.api.websocket import device_gateway

api_router = APIRouter()
owner_router = APIRouter(
    dependencies=[Depends(require_owner)],
    responses={
        401: {"description": "Missing or invalid owner credential"},
        503: {"description": "Owner API authentication is not configured"},
    },
)
owner_router.include_router(chat.router)
owner_router.include_router(memories.router)

api_router.include_router(health.router)
api_router.include_router(owner_router)
api_router.include_router(devices.router)
api_router.include_router(device_gateway.router)
