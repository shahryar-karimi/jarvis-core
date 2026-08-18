import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_chat_uses_memory_and_provider(
    app,
    fake_provider,
    fake_memory_repository,
    owner_headers,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed memory through the public API so the test covers the actual route.
        memory_response = await client.put(
            "/api/v1/memories",
            headers=owner_headers,
            json={
                "category": "preferences",
                "key": "favorite_editor",
                "value": "VS Code",
            },
        )
        assert memory_response.status_code == 200

        response = await client.post(
            "/api/v1/chat",
            headers=owner_headers,
            json={"message": "Which editor do I prefer?"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "text": "JARVIS:Which editor do I prefer?",
        "provider": "fake",
        "model": "fake-1",
    }

    assert len(fake_provider.requests) == 1
    ai_request = fake_provider.requests[0]
    assert ai_request.user_message == "Which editor do I prefer?"
    assert "Favorite Editor: VS Code" in ai_request.system_prompt
    assert "Your name is JARVIS" in ai_request.system_prompt


@pytest.mark.asyncio
async def test_chat_rejects_empty_message(app, owner_headers) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            headers=owner_headers,
            json={"message": ""},
        )

    assert response.status_code == 422
