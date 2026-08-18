import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_memory_crud_round_trip(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.put(
            "/api/v1/memories",
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

        list_response = await client.get("/api/v1/memories")
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1
        assert list_response.json()[0]["value"] == "VS Code"

        update_response = await client.put(
            "/api/v1/memories",
            json={
                "category": "preferences",
                "key": "favorite_editor",
                "value": "PyCharm",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["value"] == "PyCharm"

        delete_response = await client.delete(
            "/api/v1/memories/preferences/favorite_editor"
        )
        assert delete_response.status_code == 204

        final_response = await client.get("/api/v1/memories")
        assert final_response.status_code == 200
        assert final_response.json() == []


@pytest.mark.asyncio
async def test_memory_key_validation_rejects_unsafe_key(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/v1/memories",
            json={
                "category": "preferences",
                "key": "this key has spaces",
                "value": "anything",
            },
        )

    assert response.status_code == 422
