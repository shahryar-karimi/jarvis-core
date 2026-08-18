import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_readiness_service
from app.application.readiness_service import ReadinessReport


class StubReadinessService:
    def __init__(self, report: ReadinessReport) -> None:
        self._report = report

    async def check(self) -> ReadinessReport:
        return self._report


@pytest.mark.asyncio
async def test_health_endpoint_returns_service_status(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "jarvis-core",
        "environment": "test",
    }


@pytest.mark.asyncio
async def test_readiness_endpoint_returns_200_when_database_schema_is_ready(
    app,
) -> None:
    app.dependency_overrides[get_readiness_service] = lambda: StubReadinessService(
        ReadinessReport(
            ready=True,
            database="ok",
            migrations="current",
        )
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "jarvis-core",
        "checks": {
            "database": "ok",
            "migrations": "current",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database", "migrations"),
    [
        ("unavailable", "unknown"),
        ("ok", "outdated"),
        ("ok", "unknown"),
    ],
)
async def test_readiness_endpoint_returns_safe_503_when_not_ready(
    app,
    database: str,
    migrations: str,
) -> None:
    app.dependency_overrides[get_readiness_service] = lambda: StubReadinessService(
        ReadinessReport(
            ready=False,
            database=database,
            migrations=migrations,
        )
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "jarvis-core",
        "checks": {
            "database": database,
            "migrations": migrations,
        },
    }
