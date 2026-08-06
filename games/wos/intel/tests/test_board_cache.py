"""Board snapshot memory: TTL math, viable-left accounting, gate semantics."""

from __future__ import annotations

from typing import Any

import pytest
from games.wos.intel import board_cache


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self.store.get(key)


# --- pure helpers ------------------------------------------------------------


def test_board_ttl_follows_refresh_timer_under_cap() -> None:
    assert board_cache.board_ttl_s(300) == 300


def test_board_ttl_capped_at_15_minutes() -> None:
    assert board_cache.board_ttl_s(12_000) == 900


def test_board_ttl_unknown_timer_falls_back_to_cap() -> None:
    assert board_cache.board_ttl_s(None) == 900
    assert board_cache.board_ttl_s(0) == 900
    assert board_cache.board_ttl_s(-5) == 900


def test_board_ttl_floor_prevents_instant_expiry() -> None:
    assert board_cache.board_ttl_s(1) == 5


def test_viable_left_after_tap_consumes_one() -> None:
    assert board_cache.viable_left_after(3, tapped=True) == 2
    assert board_cache.viable_left_after(3, tapped=False) == 3
    assert board_cache.viable_left_after(0, tapped=True) == 0


# --- Redis round trip + gate -------------------------------------------------


@pytest.mark.asyncio
async def test_save_load_round_trip_with_refresh_ttl() -> None:
    redis = _FakeRedis()
    ok = await board_cache.save_board(
        redis, "p1", detected=4, viable_left=2, refresh_in_s=120, now=1000.0
    )
    assert ok is True
    assert redis.ttls[board_cache.board_key("p1")] == 120

    snap = await board_cache.load_board(redis, "p1")
    assert snap == {"viable_left": 2, "detected": 4, "captured_at": 1000.0}
    assert await board_cache.board_exhausted(redis, "p1") is False


@pytest.mark.asyncio
async def test_exhausted_when_snapshot_says_zero_left() -> None:
    redis = _FakeRedis()
    await board_cache.save_board(
        redis, "p1", detected=1, viable_left=0, refresh_in_s=None, now=1000.0
    )
    assert redis.ttls[board_cache.board_key("p1")] == 900
    assert await board_cache.board_exhausted(redis, "p1") is True


@pytest.mark.asyncio
async def test_absent_snapshot_is_not_exhausted() -> None:
    assert await board_cache.board_exhausted(_FakeRedis(), "p1") is False


@pytest.mark.asyncio
async def test_gate_is_failure_silent() -> None:
    class _Broken:
        async def get(self, key: str) -> Any:
            msg = "redis down"
            raise ConnectionError(msg)

    assert await board_cache.board_exhausted(_Broken(), "p1") is False
    assert await board_cache.load_board(_Broken(), "p1") is None


@pytest.mark.asyncio
async def test_garbage_payload_is_not_exhausted() -> None:
    redis = _FakeRedis()
    redis.store[board_cache.board_key("p1")] = "not json"
    assert await board_cache.board_exhausted(redis, "p1") is False
