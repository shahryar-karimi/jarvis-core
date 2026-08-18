from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.dependencies import ReadinessServiceDep, SettingsDep

router = APIRouter(prefix="/health", tags=["health"])


class ReadinessChecks(BaseModel):
    database: Literal["ok", "unavailable"]
    migrations: Literal["current", "outdated", "unknown"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: Literal["jarvis-core"]
    checks: ReadinessChecks


@router.get("")
async def health(settings: SettingsDep) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "jarvis-core",
        "environment": settings.environment,
    }


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Database unavailable or schema is not current",
        }
    },
)
async def readiness(
    response: Response,
    readiness_service: ReadinessServiceDep,
) -> ReadinessResponse:
    report = await readiness_service.check()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if report.ready else "not_ready",
        service="jarvis-core",
        checks=ReadinessChecks(
            database=report.database,
            migrations=report.migrations,
        ),
    )
