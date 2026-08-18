import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings


_owner_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OwnerBearer",
    bearerFormat="opaque",
    description="Owner credential for chat, memory, and task APIs.",
)
_PLACEHOLDER_OWNER_TOKENS = {
    "change-me",
    "replace-me",
    "replace-with-a-random-secret",
}


def require_owner(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_owner_bearer),
    ],
) -> None:
    settings = cast(Settings, request.app.state.settings)
    configured = settings.owner_token
    expected = configured.get_secret_value().strip() if configured else ""
    if (
        len(expected) < 32
        or expected.casefold() in _PLACEHOLDER_OWNER_TOKENS
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Owner API authentication is not configured.",
        )

    supplied = credentials.credentials if credentials else ""
    if (
        credentials is None
        or credentials.scheme.casefold() != "bearer"
        or not secrets.compare_digest(
            supplied.encode("utf-8"),
            expected.encode("utf-8"),
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid owner credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )
