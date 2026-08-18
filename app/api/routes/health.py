from fastapi import APIRouter

from app.api.dependencies import SettingsDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(settings: SettingsDep) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "jarvis-core",
        "environment": settings.environment,
    }
