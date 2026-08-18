"""Opt-in live Gemini test.

This makes a real provider request and may consume quota/tokens.
Run with:
    Set RUN_LIVE_AI_TESTS=1 in .env, then run:
    python -m pytest -m live tests/test_gemini_smoke.py -v

PowerShell:
    $env:RUN_LIVE_AI_TESTS = "1"
    python -m pytest -m live tests/test_gemini_smoke.py -v
"""

import pytest
from google.genai.errors import ClientError

from app.core.config import get_settings
from app.domain.ai import AIRequest
from app.infrastructure.ai.gemini import GeminiProvider


pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_real_gemini_returns_text(external_test_settings) -> None:
    if not external_test_settings.run_live_ai_tests:
        pytest.skip("Set RUN_LIVE_AI_TESTS=1 in .env to call Gemini")

    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key.strip().lower() in {
        "replace-me",
        "changeme",
    }:
        pytest.fail(
            "RUN_LIVE_AI_TESTS is enabled but JARVIS_GEMINI_API_KEY is not configured"
        )

    provider = GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    rejection: str | None = None
    try:
        response = await provider.complete(
            AIRequest(
                user_message="Reply with only the word OK.",
                system_prompt="You are a connectivity test. Follow the request exactly.",
            )
        )
    except ClientError as error:
        rejection = (
            f"Gemini request rejected ({error.code} {error.status}): {error.message}"
        )

    if rejection:
        pytest.fail(
            rejection,
            pytrace=False,
        )

    assert response.provider == "gemini"
    assert response.model == settings.gemini_model
    assert response.text.strip()
