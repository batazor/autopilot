from __future__ import annotations

import pytest

from worker.onboarding_phase import (
    ACTIVE_PLAYER_FIELD,
    FURNACE_LEVEL_FIELDS,
    ONBOARDING_EXIT_FIELD,
    ONBOARDING_STATE_FIELDS,
    onboarding_active,
)


class _FakeRedis:
    def __init__(
        self,
        *,
        active_player: object = None,
        sawmill: object = None,
        furnace: object = None,
        furnace_reader: object = None,
    ) -> None:
        durable, reader = FURNACE_LEVEL_FIELDS
        self._values = {
            ACTIVE_PLAYER_FIELD: active_player,
            ONBOARDING_EXIT_FIELD: sawmill,
            durable: furnace,
            reader: furnace_reader,
        }
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
    assert r.asked == [("wos:instance:bs1:state", ONBOARDING_STATE_FIELDS)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sawmill", "expected"),
    [("0", True), ("", True), ("junk", True), ("1", False), ("2", False), (b"1", False)],
)
async def test_sawmill_level_gates_phase_without_player(sawmill, expected) -> None:
    # No resolved player, low furnace → the Sawmill build is the deciding signal.
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
    # Fresh onboarding: no resolved player, no Sawmill, low furnace → onboarding.
    assert await onboarding_active(_FakeRedis(active_player="", sawmill=None), "bs1") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("furnace", "expected"),
    [("4", True), ("5", False), ("22", False), (b"22", False), ("junk", True), ("", True)],
)
async def test_furnace_exits_onboarding_without_player_or_sawmill(furnace, expected) -> None:
    # The bs3 deadlock: a developed account right after a worker restart — no
    # resolved active_player yet, no bot-recorded Sawmill — must NOT read as
    # onboarding once the furnace is past the early-tutorial threshold, or the
    # popup dismissers stay gated off and a login modal wedges the instance at
    # idle while the node-gated who_i_am can never run to resolve identity.
    assert await onboarding_active(_FakeRedis(furnace=furnace), "bs1") is expected


@pytest.mark.asyncio
async def test_furnace_reader_field_also_counts() -> None:
    # The onboarding furnace reader writes ``buildings.furnace.level``; accept it
    # even when the durable ``buildings.levels.furnace`` mirror is absent.
    assert await onboarding_active(_FakeRedis(furnace_reader="22"), "bs1") is False


@pytest.mark.asyncio
async def test_low_furnace_is_still_onboarding() -> None:
    # A genuine first-run tutorial (furnace below threshold, no player/sawmill)
    # must still read as onboarding so the dismissers don't fight the tutorial.
    assert await onboarding_active(_FakeRedis(furnace="3"), "bs1") is True
