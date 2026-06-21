from __future__ import annotations

import pytest

from worker.onboarding_phase import (
    ACTIVE_PLAYER_FIELD,
    ONBOARDING_EXIT_FIELD,
    onboarding_active,
)


class _FakeRedis:
    def __init__(self, *, active_player: object = None, sawmill: object = None) -> None:
        self._values = {ACTIVE_PLAYER_FIELD: active_player, ONBOARDING_EXIT_FIELD: sawmill}
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    async def hmget(self, key: str, fields: list[str]):
        self.asked.append((key, tuple(fields)))
        return [self._values.get(f) for f in fields]


@pytest.mark.asyncio
async def test_no_redis_is_not_onboarding() -> None:
    # Degraded / unit-test mode keeps the dismissers' pre-existing behaviour.
    assert await onboarding_active(None, "bs1") is False


@pytest.mark.asyncio
async def test_absent_signals_is_onboarding() -> None:
    r = _FakeRedis()
    assert await onboarding_active(r, "bs1") is True
    assert r.asked == [
        ("wos:instance:bs1:state", (ACTIVE_PLAYER_FIELD, ONBOARDING_EXIT_FIELD))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sawmill", "expected"),
    [("0", True), ("", True), ("junk", True), ("1", False), ("2", False), (b"1", False)],
)
async def test_sawmill_level_gates_phase_without_player(sawmill, expected) -> None:
    # No resolved player → the Sawmill build is the deciding signal.
    assert await onboarding_active(_FakeRedis(sawmill=sawmill), "bs1") is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("active_player", ["401227964", b"401227964"])
async def test_resolved_player_exits_onboarding_even_without_sawmill(active_player) -> None:
    # Developed account the bot never personally onboarded: who_i_am resolved a
    # real player but no bot-recorded Sawmill. Must NOT read as onboarding (else
    # the popup dismissers stay gated off and login modals wedge the worker).
    assert await onboarding_active(_FakeRedis(active_player=active_player), "bs1") is False


@pytest.mark.asyncio
async def test_empty_player_falls_back_to_sawmill() -> None:
    # Fresh onboarding: no resolved player and no Sawmill → still onboarding.
    assert await onboarding_active(_FakeRedis(active_player="", sawmill=None), "bs1") is True
