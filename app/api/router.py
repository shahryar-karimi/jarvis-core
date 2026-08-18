from fastapi import APIRouter

from app.api.routes import chat, devices, health, memories
from app.api.websocket import device_gateway

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(memories.router)
api_router.include_router(devices.router)
api_router.include_router(device_gateway.router)
