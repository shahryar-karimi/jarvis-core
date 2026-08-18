from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.application.memory_service import MemoryNotFoundError


async def memory_not_found_handler(
    _: Request,
    error: MemoryNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(error)},
    )
