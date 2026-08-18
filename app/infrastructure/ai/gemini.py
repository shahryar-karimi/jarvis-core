from app.domain.ai import AICapability, AIProvider, AIRequest, AIResponse


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("Gemini API key is not configured")
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def supports(self, capability: AICapability) -> bool:
        return capability in {
            AICapability.TEXT,
            AICapability.TOOL_CALLING,
            AICapability.VISION,
            AICapability.STRUCTURED_OUTPUT,
        }

    async def complete(self, request: AIRequest) -> AIResponse:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=request.user_message,
            config=types.GenerateContentConfig(system_instruction=request.system_prompt),
        )
        return AIResponse(
            text=response.text or "",
            provider=self.name,
            model=self._model,
        )

    async def aclose(self) -> None:
        try:
            await self._client.aio.aclose()
        finally:
            self._client.close()
