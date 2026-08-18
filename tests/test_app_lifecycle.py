from __future__ import annotations

import pytest

from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings
from app.main import create_app


class FakeDatabase:
    def __init__(self) -> None:
        self.session_factory = object()
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


class FakeAIProvider:
    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


class FakeHive:
    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


@pytest.mark.asyncio
async def test_lifespan_builds_shared_resources_once_and_closes_them(
    test_settings: Settings,
) -> None:
    databases: list[FakeDatabase] = []
    providers: list[FakeAIProvider] = []
    hives: list[FakeHive] = []

    def database_factory(database_url: str) -> FakeDatabase:
        assert database_url == test_settings.database_url
        database = FakeDatabase()
        databases.append(database)
        return database

    def ai_provider_factory(settings: Settings) -> FakeAIProvider:
        assert settings is test_settings
        provider = FakeAIProvider()
        providers.append(provider)
        return provider

    def hive_factory(settings: Settings, session_factory: object) -> FakeHive:
        assert settings is test_settings
        assert session_factory is databases[0].session_factory
        hive = FakeHive()
        hives.append(hive)
        return hive

    application = create_app(
        test_settings,
        database_factory=database_factory,
        ai_provider_factory=ai_provider_factory,
        hive_factory=hive_factory,
    )

    assert application.state.settings is test_settings
    assert databases == []
    assert providers == []
    assert not hasattr(application.state, "database")
    assert not hasattr(application.state, "ai_provider")
    assert not hasattr(application.state, "prompt_builder")
    assert not hasattr(application.state, "hive")

    async with application.router.lifespan_context(application):
        assert len(databases) == 1
        assert len(providers) == 1
        assert application.state.database is databases[0]
        assert application.state.ai_provider is providers[0]
        assert isinstance(application.state.prompt_builder, PromptBuilder)
        assert application.state.hive is hives[0]

        database_resource = application.state.database
        provider_resource = application.state.ai_provider
        prompt_builder_resource = application.state.prompt_builder
        hive_resource = application.state.hive

        assert application.state.database is database_resource
        assert application.state.ai_provider is provider_resource
        assert application.state.prompt_builder is prompt_builder_resource
        assert application.state.hive is hive_resource
        assert len(databases) == 1
        assert len(providers) == 1

    assert databases[0].dispose_calls == 1
    assert providers[0].aclose_calls == 1
    assert hives[0].aclose_calls == 1
    assert not hasattr(application.state, "database")
    assert not hasattr(application.state, "ai_provider")
    assert not hasattr(application.state, "prompt_builder")
    assert not hasattr(application.state, "hive")
    assert application.state.settings is test_settings


@pytest.mark.asyncio
async def test_lifespan_disposes_database_if_provider_creation_fails(
    test_settings: Settings,
) -> None:
    database = FakeDatabase()

    def database_factory(database_url: str) -> FakeDatabase:
        assert database_url == test_settings.database_url
        return database

    def failing_provider_factory(settings: Settings) -> FakeAIProvider:
        assert settings is test_settings
        raise RuntimeError("provider initialization failed")

    application = create_app(
        test_settings,
        database_factory=database_factory,
        ai_provider_factory=failing_provider_factory,
    )

    with pytest.raises(RuntimeError, match="provider initialization failed"):
        async with application.router.lifespan_context(application):
            pytest.fail("lifespan yielded after provider initialization failed")

    assert database.dispose_calls == 1
    assert not hasattr(application.state, "database")
    assert not hasattr(application.state, "ai_provider")
    assert not hasattr(application.state, "prompt_builder")
    assert not hasattr(application.state, "hive")
    assert application.state.settings is test_settings


@pytest.mark.asyncio
async def test_lifespan_closes_earlier_resources_if_hive_creation_fails(
    test_settings: Settings,
) -> None:
    database = FakeDatabase()
    provider = FakeAIProvider()

    def failing_hive_factory(
        settings: Settings,
        session_factory: object,
    ) -> FakeHive:
        assert settings is test_settings
        assert session_factory is database.session_factory
        raise RuntimeError("hive initialization failed")

    application = create_app(
        test_settings,
        database_factory=lambda _: database,
        ai_provider_factory=lambda _: provider,
        hive_factory=failing_hive_factory,
    )

    with pytest.raises(RuntimeError, match="hive initialization failed"):
        async with application.router.lifespan_context(application):
            pytest.fail("lifespan yielded after hive initialization failed")

    assert provider.aclose_calls == 1
    assert database.dispose_calls == 1
    assert not hasattr(application.state, "database")
    assert not hasattr(application.state, "ai_provider")
    assert not hasattr(application.state, "prompt_builder")
    assert not hasattr(application.state, "hive")
