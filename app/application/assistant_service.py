from app.application.memory_service import MemoryService
from app.application.prompt_builder import PromptBuilder
from app.domain.ai import AIProvider, AIRequest, AIResponse


class AssistantService:
    """Use-case orchestrator: memory/context -> prompt -> selected provider."""

    def __init__(
        self,
        provider: AIProvider,
        memory_service: MemoryService,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._provider = provider
        self._memory_service = memory_service
        self._prompt_builder = prompt_builder

    async def chat(self, message: str) -> AIResponse:
        memories = await self._memory_service.list_memories()
        system_prompt = self._prompt_builder.build(memories)
        return await self._provider.complete(
            AIRequest(user_message=message, system_prompt=system_prompt)
        )
