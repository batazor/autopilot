"""Tests for the ``device_level`` gate in ``RedisQueue.pop_due``.

When ``active_player`` is empty on the instance state, only scenarios marked
``device_level: true`` may run.  Player-bound scenarios (default) must wait
until ``who_i_am`` populates ``active_player``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scheduler.queue import RedisQueue


class _FakeAsyncRedis:
    """Minimal async Redis fake supporting hash + sorted-set ops used by ``pop_due``."""

    def __init__(self, *, active_player: str = "") -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        if active_player:
            # Mimic instance state hash that ``who_i_am`` would have written.
            self._hashes["wos:instance:bs1:state"] = {"active_player": active_player}
        self._zsets: dict[str, list[tuple[str, float]]] = {}
        self._sets: dict[str, set[str]] = {}

    async def hget(self, key: str, field: str) -> str | None:
        row = self._hashes.get(key)
        if not row:
            return None
        return row.get(field)

    async def zrangebyscore(self, key: str, _lo: Any, _hi: Any) -> list[str]:
        return [raw for raw, _score in self._zsets.get(key, [])]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self._zsets.setdefault(key, [])
        for raw, score in mapping.items():
            bucket.append((raw, float(score)))
        return len(mapping)

    async def zrem(self, key: str, raw: str) -> int:
        bucket = self._zsets.get(key, [])
        before = len(bucket)
        bucket[:] = [(r, s) for r, s in bucket if r != raw]
        return before - len(bucket)

    async def sadd(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    async def srem(self, *_args: Any, **_kwargs: Any) -> int:
        return 0


def _enqueue(
    redis: _FakeAsyncRedis,
    *,
    task_type: str,
    instance_id: str = "bs1",
    priority: int = 50_000,
    run_at: float = 0.0,
) -> None:
    body = {
        "task_id": f"t-{task_type}",
        "player_id": "",
        "task_type": task_type,
        "priority": priority,
        "run_at": run_at,
        "instance_id": instance_id,
    }
    raw = json.dumps(body)
    redis._zsets.setdefault(f"wos:queue:{instance_id}", []).append((raw, run_at))


def _make_queue(redis: _FakeAsyncRedis, monkeypatch: Any) -> RedisQueue:
    q = object.__new__(RedisQueue)
    q._redis = redis  # type: ignore[attr-defined]
    # Avoid loading real settings (and the device-config dependency it pulls in).
    monkeypatch.setattr(q, "_players_for_instance", lambda _iid: set(), raising=False)
    return q


@pytest.mark.asyncio
async def test_pop_due_blocks_player_bound_scenario_when_active_player_missing(
    monkeypatch: Any,
) -> None:
    redis = _FakeAsyncRedis(active_player="")
    _enqueue(redis, task_type="assign_worker", priority=80_000)
    q = _make_queue(redis, monkeypatch)

    item = await q.pop_due("bs1", current_screen="main_city")

    assert item is None, "assign_worker is player-bound and must be gated"


@pytest.mark.asyncio
async def test_pop_due_allows_device_level_scenario_when_active_player_missing(
    monkeypatch: Any,
) -> None:
    redis = _FakeAsyncRedis(active_player="")
    _enqueue(redis, task_type="who_i_am", priority=82_000)
    q = _make_queue(redis, monkeypatch)

    item = await q.pop_due("bs1", current_screen="main_city")

    assert item is not None
    assert item.task_type == "who_i_am"


@pytest.mark.asyncio
async def test_pop_due_prefers_device_level_when_player_bound_outranks(
    monkeypatch: Any,
) -> None:
    """Higher-priority routine task must NOT preempt seed when player is unknown."""
    redis = _FakeAsyncRedis(active_player="")
    _enqueue(redis, task_type="assign_worker", priority=80_000)
    _enqueue(redis, task_type="who_i_am", priority=82_000)
    q = _make_queue(redis, monkeypatch)

    item = await q.pop_due("bs1", current_screen="main_city")

    assert item is not None
    assert item.task_type == "who_i_am"


@pytest.mark.asyncio
async def test_pop_due_releases_player_bound_scenario_once_active_player_set(
    monkeypatch: Any,
) -> None:
    redis = _FakeAsyncRedis(active_player="765502864")
    _enqueue(redis, task_type="assign_worker", priority=80_000)
    q = _make_queue(redis, monkeypatch)

    item = await q.pop_due("bs1", current_screen="main_city")

    assert item is not None
    assert item.task_type == "assign_worker"


def test_task_types_device_level_includes_bootstrap_and_excludes_player_bound() -> None:
    names = RedisQueue._task_types_device_level()

    # Identity probes
    assert "who_i_am" in names
    assert "where_i_am" in names
    # Tutorial / popup dismissals
    assert "skip_button" in names
    assert "hand_pointer" in names
    assert "tap_reconnect_button" in names
    # Player-bound — must NOT be marked
    assert "assign_worker" not in names
    assert "read_mail_gifts" not in names
    assert "chapter_task_router" not in names
    assert "new_chapter" not in names
