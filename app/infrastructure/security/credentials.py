from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hmac
import secrets
from uuid import UUID, uuid4


IdFactory = Callable[[], UUID]
TokenFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class IssuedDeviceCredential:
    id: UUID
    token: str
    digest: bytes

    def __repr__(self) -> str:
        return (
            "IssuedDeviceCredential("
            f"id={self.id!r}, token='<redacted>', digest='<redacted>')"
        )


class DeviceCredentialCodec:
    """Create and verify opaque device tokens without storing the raw token."""

    TOKEN_PREFIX = "jv1"

    def __init__(
        self,
        digest_key: bytes,
        *,
        id_factory: IdFactory = uuid4,
        token_factory: TokenFactory | None = None,
    ) -> None:
        if len(digest_key) < 32:
            raise ValueError("credential digest key must contain at least 32 bytes")
        self._digest_key = bytes(digest_key)
        self._id_factory = id_factory
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def issue(self) -> IssuedDeviceCredential:
        credential_id = self._id_factory()
        token_secret = self._token_factory()
        if len(token_secret) < 16:
            raise ValueError("device token secret is too short")
        if "." in token_secret:
            raise ValueError("device token secret cannot contain periods")
        token = f"{self.TOKEN_PREFIX}.{credential_id}.{token_secret}"
        return IssuedDeviceCredential(
            id=credential_id,
            token=token,
            digest=self.digest(token),
        )

    def digest(self, token: str) -> bytes:
        return hmac.digest(
            self._digest_key,
            token.encode("utf-8"),
            "sha256",
        )

    def matches(self, token: str, expected_digest: bytes) -> bool:
        return hmac.compare_digest(expected_digest, self.digest(token))

    @classmethod
    def parse_token_id(cls, token: str) -> UUID | None:
        if len(token) > 256:
            return None
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != cls.TOKEN_PREFIX or not parts[2]:
            return None
        try:
            return UUID(parts[1])
        except ValueError:
            return None
