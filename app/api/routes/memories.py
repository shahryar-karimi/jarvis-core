from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_memory_service
from app.api.schemas import MemoryResponse, MemoryUpsertRequest
from app.application.memory_service import MemoryService
from app.domain.memory import MemoryCategory


router = APIRouter(prefix="/memories", tags=["memory"])
MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]


@router.get("", response_model=list[MemoryResponse])
async def list_memories(service: MemoryServiceDep) -> list[MemoryResponse]:
    entries = await service.list_memories()
    return [
        MemoryResponse(
            category=entry.category,
            key=entry.key,
            value=entry.value,
            updated_at=entry.updated_at,
        )
        for entry in entries
    ]


@router.put("", response_model=MemoryResponse)
async def upsert_memory(
    request: MemoryUpsertRequest,
    service: MemoryServiceDep,
) -> MemoryResponse:
    entry = await service.upsert_memory(
        request.category,
        request.key,
        request.value,
    )
    return MemoryResponse(
        category=entry.category,
        key=entry.key,
        value=entry.value,
        updated_at=entry.updated_at,
    )


@router.delete(
    "/{category}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Memory not found"}},
)
async def delete_memory(
    category: MemoryCategory,
    key: str,
    service: MemoryServiceDep,
) -> Response:
    await service.delete_memory(category, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
