from app.application.prompt_builder import PromptBuilder
from app.domain.ai import AIProvider, AIRequest, AIResponse
from app.domain.memory import MemoryRepository


class AssistantService:
    """Use-case orchestrator: memory/context -> prompt -> selected provider."""

    def __init__(
        self,
        provider: AIProvider,
        memory_repository: MemoryRepository,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._provider = provider
        self._memory_repository = memory_repository
        self._prompt_builder = prompt_builder

    async def chat(self, message: str) -> AIResponse:
        memories = await self._memory_repository.list_all()
        system_prompt = self._prompt_builder.build(memories)
        return await self._provider.complete(
            AIRequest(user_message=message, system_prompt=system_prompt)
        )
