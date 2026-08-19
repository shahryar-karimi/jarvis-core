from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.devices import DeviceCapability
from app.infrastructure.devices import InMemoryDeviceRegistry
from app.infrastructure.security import (
    InMemoryDeviceIdentity,
    InMemoryDevicePairing,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
PAIRING_ID = UUID("10000000-0000-0000-0000-000000000001")
DEVICE_ID = UUID("20000000-0000-0000-0000-000000000002")
TOKEN_ID = UUID("30000000-0000-0000-0000-000000000003")
OTHER_DEVICE_ID = UUID("40000000-0000-0000-0000-000000000004")
OTHER_TOKEN_ID = UUID("50000000-0000-0000-0000-000000000005")
PAIRING_SECRET = "pairing-secret-with-enough-entropy"
TOKEN_SECRET = "device-token-with-enough-entropy"


class MutableClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class SequenceIds:
    def __init__(self, *values: UUID) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        return next(self._values)


def build_security_resources(
    *,
    clock: MutableClock | None = None,
) -> tuple[
    InMemoryDeviceRegistry,
    InMemoryDeviceIdentity,
    InMemoryDevicePairing,
    MutableClock,
]:
    resolved_clock = clock or MutableClock()
    registry = InMemoryDeviceRegistry()
    identity = InMemoryDeviceIdentity(
        digest_key=b"identity-digest-key" * 2,
        id_factory=lambda: TOKEN_ID,
        token_factory=lambda: TOKEN_SECRET,
    )
    pairing = InMemoryDevicePairing(
        registry,
        identity,
        clock=resolved_clock,
        digest_key=b"pairing-digest-key-" * 2,
        id_factory=SequenceIds(PAIRING_ID, DEVICE_ID),
        token_factory=lambda: PAIRING_SECRET,
    )
    return registry, identity, pairing, resolved_clock


@pytest.mark.asyncio
async def test_pairing_and_credentials_store_digests_and_redact_secrets() -> None:
    registry, identity, pairing, _ = build_security_resources()

    challenge = await pairing.create(
        frozenset({DeviceCapability.OPEN_APP}),
        timedelta(minutes=5),
    )

    assert challenge.secret == PAIRING_SECRET
    assert PAIRING_SECRET not in repr(challenge)
    assert "<redacted>" in repr(challenge)

    pairing_record = pairing._pairings[challenge.id]
    assert pairing_record.secret_digest != PAIRING_SECRET.encode("utf-8")
    assert len(pairing_record.secret_digest) == 32
    assert PAIRING_SECRET not in repr(pairing_record)

    credential = await pairing.claim(
        challenge.id,
        challenge.secret,
        "Office workstation",
    )

    assert credential is not None
    assert credential.device.id == DEVICE_ID
    assert credential.token.startswith(f"jv1.{TOKEN_ID}.")
    assert credential.token not in repr(credential)
    assert "<redacted>" in repr(credential)
    assert await registry.get(DEVICE_ID) == credential.device

    identity_record = identity._credentials[TOKEN_ID]
    assert identity_record.digest != credential.token.encode("utf-8")
    assert len(identity_record.digest) == 32
    assert credential.token not in repr(identity._credentials)


@pytest.mark.asyncio
async def test_pairing_claim_is_correct_one_time_and_rejects_wrong_secret() -> None:
    registry, identity, pairing, _ = build_security_resources()
    challenge = await pairing.create(
        frozenset({DeviceCapability.OPEN_APP, DeviceCapability.SYSTEM}),
        timedelta(minutes=5),
    )

    assert (
        await pairing.claim(
            challenge.id,
            "wrong-pairing-secret",
            "Impostor",
        )
        is None
    )

    credential = await pairing.claim(
        challenge.id,
        challenge.secret,
        "Office workstation",
    )

    assert credential is not None
    assert credential.device.name == "Office workstation"
    assert credential.device.granted_capabilities == frozenset(
        {DeviceCapability.OPEN_APP, DeviceCapability.SYSTEM}
    )
    assert await identity.authenticate(credential.token) == credential.device.id
    assert await registry.list_all() == [credential.device]

    assert (
        await pairing.claim(
            challenge.id,
            challenge.secret,
            "Replay attempt",
        )
        is None
    )
    assert await registry.list_all() == [credential.device]


@pytest.mark.asyncio
async def test_pairing_claim_rejects_a_challenge_at_its_expiry_boundary() -> None:
    clock = MutableClock()
    registry, _, pairing, _ = build_security_resources(clock=clock)
    challenge = await pairing.create(
        frozenset({DeviceCapability.FILES}),
        timedelta(minutes=5),
    )
    clock.current = challenge.expires_at

    assert (
        await pairing.claim(
            challenge.id,
            challenge.secret,
            "Late workstation",
        )
        is None
    )
    assert await registry.list_all() == []


@pytest.mark.asyncio
async def test_device_token_is_bound_to_one_device_and_revoke_is_scoped() -> None:
    token_ids = SequenceIds(TOKEN_ID, OTHER_TOKEN_ID)
    token_secrets = iter((TOKEN_SECRET, "other-device-token-secret"))
    identity = InMemoryDeviceIdentity(
        digest_key=b"identity-digest-key" * 2,
        id_factory=token_ids,
        token_factory=lambda: next(token_secrets),
    )

    token = await identity.issue(DEVICE_ID)
    other_token = await identity.issue(OTHER_DEVICE_ID)

    assert await identity.authenticate(token) == DEVICE_ID
    assert await identity.authenticate(other_token) == OTHER_DEVICE_ID
    assert await identity.authenticate(f"{token}tampered") is None

    token_parts = token.split(".")
    forged_for_other_id = f"{token_parts[0]}.{OTHER_TOKEN_ID}.{token_parts[2]}"
    assert await identity.authenticate(forged_for_other_id) is None

    await identity.revoke(DEVICE_ID)

    assert await identity.authenticate(token) is None
    assert await identity.authenticate(other_token) == OTHER_DEVICE_ID


@pytest.mark.asyncio
async def test_concurrent_pairing_claim_has_exactly_one_winner() -> None:
    registry, identity, pairing, _ = build_security_resources()
    challenge = await pairing.create(
        frozenset({DeviceCapability.OPEN_APP}),
        timedelta(minutes=5),
    )

    claims = await asyncio.gather(
        pairing.claim(challenge.id, challenge.secret, "First claimant"),
        pairing.claim(challenge.id, challenge.secret, "Second claimant"),
    )

    credentials = [credential for credential in claims if credential is not None]
    assert len(credentials) == 1
    assert sum(credential is None for credential in claims) == 1
    assert await registry.list_all() == [credentials[0].device]
    assert await identity.authenticate(credentials[0].token) == DEVICE_ID


@pytest.mark.asyncio
async def test_pairing_claim_rolls_back_identity_if_registration_fails() -> None:
    class RejectingRegistry(InMemoryDeviceRegistry):
        async def add(self, device) -> None:
            raise RuntimeError("registry unavailable")

    registry = RejectingRegistry()
    identity = InMemoryDeviceIdentity(
        digest_key=b"identity-digest-key" * 2,
        id_factory=lambda: TOKEN_ID,
        token_factory=lambda: TOKEN_SECRET,
    )
    pairing = InMemoryDevicePairing(
        registry,
        identity,
        clock=MutableClock(),
        digest_key=b"pairing-digest-key-" * 2,
        id_factory=SequenceIds(PAIRING_ID, DEVICE_ID),
        token_factory=lambda: PAIRING_SECRET,
    )
    challenge = await pairing.create(
        frozenset({DeviceCapability.OPEN_APP}),
        timedelta(minutes=5),
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        await pairing.claim(challenge.id, challenge.secret, "Workstation")

    assert identity._credentials == {}
    assert challenge.id in pairing._pairings


@pytest.mark.asyncio
async def test_cancelled_pairing_claim_rolls_back_issued_identity() -> None:
    class BlockingRegistry(InMemoryDeviceRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.add_started = asyncio.Event()
            self.release_add = asyncio.Event()

        async def add(self, device) -> None:
            await super().add(device)
            self.add_started.set()
            await self.release_add.wait()

    registry = BlockingRegistry()
    identity = InMemoryDeviceIdentity(
        digest_key=b"identity-digest-key" * 2,
        id_factory=lambda: TOKEN_ID,
        token_factory=lambda: TOKEN_SECRET,
    )
    pairing = InMemoryDevicePairing(
        registry,
        identity,
        clock=MutableClock(),
        digest_key=b"pairing-digest-key-" * 2,
        id_factory=SequenceIds(PAIRING_ID, DEVICE_ID),
        token_factory=lambda: PAIRING_SECRET,
    )
    challenge = await pairing.create(
        frozenset({DeviceCapability.OPEN_APP}),
        timedelta(minutes=5),
    )
    claim = asyncio.create_task(pairing.claim(challenge.id, challenge.secret, "Workstation"))
    await asyncio.wait_for(registry.add_started.wait(), timeout=0.5)

    claim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await claim

    assert identity._credentials == {}
    assert challenge.id in pairing._pairings
    assert await registry.list_all() == []
