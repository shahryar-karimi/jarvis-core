from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.memory import MemoryCategory


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ChatResponse(BaseModel):
    text: str
    provider: str
    model: str


class MemoryUpsertRequest(BaseModel):
    category: MemoryCategory
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=4_000)


class MemoryResponse(BaseModel):
    category: MemoryCategory
    key: str
    value: str
    updated_at: datetime
