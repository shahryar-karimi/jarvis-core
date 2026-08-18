from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AICapability(StrEnum):
    TEXT = "text"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    REALTIME_AUDIO = "realtime_audio"
    STRUCTURED_OUTPUT = "structured_output"
    EMBEDDINGS = "embeddings"


@dataclass(slots=True)
class AIRequest:
    user_message: str
    system_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AIResponse:
    text: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    """Provider-neutral contract. The application layer never imports an AI SDK."""

    name: str

    @abstractmethod
    def supports(self, capability: AICapability) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def complete(self, request: AIRequest) -> AIResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release provider-owned resources when the application stops."""
