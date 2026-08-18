from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_assistant_service
from app.api.schemas import ChatRequest, ChatResponse
from app.application.assistant_service import AssistantService

router = APIRouter(prefix="/chat", tags=["conversation"])


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> ChatResponse:
    response = await service.chat(request.message)
    return ChatResponse(text=response.text, provider=response.provider, model=response.model)
