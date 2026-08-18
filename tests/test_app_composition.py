import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_routes_use_lifespan_owned_resources_without_overrides(
    tmp_path: Path,
    test_settings: Settings,
    fake_provider,
    owner_headers,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'application.db').as_posix()}"
    settings = test_settings.model_copy(update={"database_url": database_url})
    migration_config = Config(PROJECT_ROOT / "alembic.ini")
    migration_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, migration_config, "head")

    application = create_app(
        settings,
        ai_provider_factory=lambda _: fake_provider,
    )

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            memory_response = await client.put(
                "/api/v1/memories",
                headers=owner_headers,
                json={
                    "category": "preferences",
                    "key": "favorite_editor",
                    "value": "PyCharm",
                },
            )
            chat_response = await client.post(
                "/api/v1/chat",
                headers=owner_headers,
                json={"message": "Which editor do I prefer?"},
            )

        assert memory_response.status_code == 200
        assert chat_response.status_code == 200
        assert chat_response.json()["provider"] == "fake"
        assert len(fake_provider.requests) == 1
        assert "Favorite Editor: PyCharm" in (
            fake_provider.requests[0].system_prompt
        )

    assert not application.dependency_overrides
    assert not hasattr(application.state, "database")
