import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_memory_service


@pytest.mark.asyncio
async def test_memory_crud_round_trip(app, owner_headers) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.put(
            "/api/v1/memories",
            headers=owner_headers,
            json={
                "category": "preferences",
                "key": "favorite_editor",
                "value": "VS Code",
            },
        )
        assert create_response.status_code == 200
        created = create_response.json()
        assert created["category"] == "preferences"
        assert created["key"] == "favorite_editor"
        assert created["value"] == "VS Code"
        assert created["updated_at"]

        list_response = await client.get(
            "/api/v1/memories",
            headers=owner_headers,
        )
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1
        assert list_response.json()[0]["value"] == "VS Code"

        update_response = await client.put(
            "/api/v1/memories",
            headers=owner_headers,
            json={
                "category": "preferences",
                "key": "favorite_editor",
                "value": "PyCharm",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["value"] == "PyCharm"

        delete_response = await client.delete(
            "/api/v1/memories/preferences/favorite_editor",
            headers=owner_headers,
        )
        assert delete_response.status_code == 204

        final_response = await client.get(
            "/api/v1/memories",
            headers=owner_headers,
        )
        assert final_response.status_code == 200
        assert final_response.json() == []


@pytest.mark.asyncio
async def test_memory_key_validation_rejects_unsafe_key(
    app,
    owner_headers,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/v1/memories",
            headers=owner_headers,
            json={
                "category": "preferences",
                "key": "this key has spaces",
                "value": "anything",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_deleting_a_missing_memory_returns_not_found(
    app,
    owner_headers,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/v1/memories/preferences/missing_key",
            headers=owner_headers,
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Memory 'preferences/missing_key' was not found."
    }


@pytest.mark.asyncio
async def test_unexpected_memory_error_is_not_exposed(
    app,
    owner_headers,
) -> None:
    internal_detail = "database password should stay private"

    class FailingMemoryService:
        async def list_memories(self):
            raise RuntimeError(internal_detail)

    app.dependency_overrides[get_memory_service] = FailingMemoryService
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/memories",
            headers=owner_headers,
        )

    assert response.status_code == 500
    assert internal_detail not in response.text


def test_memory_delete_documents_not_found_response(app) -> None:
    delete_operation = app.openapi()["paths"][
        "/api/v1/memories/{category}/{key}"
    ]["delete"]

    assert "404" in delete_operation["responses"]
