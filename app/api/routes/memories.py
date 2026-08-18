from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_memory_repository
from app.api.schemas import MemoryResponse, MemoryUpsertRequest
from app.infrastructure.repositories.memory import SqlAlchemyMemoryRepository
from app.domain.memory import MemoryCategory

router = APIRouter(prefix="/memories", tags=["memory"])
RepoDep = Annotated[SqlAlchemyMemoryRepository, Depends(get_memory_repository)]


@router.get("", response_model=list[MemoryResponse])
async def list_memories(repository: RepoDep) -> list[MemoryResponse]:
    entries = await repository.list_all()
    return [MemoryResponse(category=e.category, key=e.key, value=e.value, updated_at=e.updated_at) for e in entries]


@router.put("", response_model=MemoryResponse)
async def upsert_memory(request: MemoryUpsertRequest, repository: RepoDep) -> MemoryResponse:
    entry = await repository.upsert(request.category, request.key, request.value)
    return MemoryResponse(category=entry.category, key=entry.key, value=entry.value, updated_at=entry.updated_at)


@router.delete("/{category}/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(category: MemoryCategory, key: str, repository: RepoDep) -> Response:
    await repository.delete(category, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
