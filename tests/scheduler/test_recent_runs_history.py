"""Integration tests for recent_runs retention + last_run_at lookup.

Covers:
* the time + count caps in :meth:`RedisQueue._append_recent_run` work together
  (count cap fires when retention seconds alone would let the ZSET grow);
* :meth:`RedisQueue.last_run_at` returns the newest timestamp matching a
  ``(task_type, player_id)`` pair, ``None`` when no match exists;
* :meth:`RedisQueue.oldest_recent_run_age` reports the age of the oldest
  surviving entry — the signal cron-history dashboards key off.

Uses the testcontainers Redis from the root ``conftest.py`` so the ZSET
semantics are real, not mocked.
"""
from __future__ import annotations

import time

import pytest

from config.loader import get_settings
from scheduler.queue import (
    RECENT_RUNS_RETENTION_CAP,
    RedisQueue,
    _last_run_key,
    _recent_runs_key,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_run_at_returns_most_recent_score(redis_async: object) -> None:
    """Newest score wins when several entries share the same prefix."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid = "bs1"
    now = time.time()

    # Three runs of the same (task_type, player_id); pick the newest.
    await q._append_recent_run(
        instance_id=iid, task_type="ping", player_id="p1", now=now - 300
    )
    await q._append_recent_run(
        instance_id=iid, task_type="ping", player_id="p1", now=now - 60
    )
    await q._append_recent_run(
        instance_id=iid, task_type="ping", player_id="p1", now=now - 10
    )
    # Another task — must not bleed through into the lookup.
    await q._append_recent_run(
        instance_id=iid, task_type="other", player_id="p1", now=now - 5
    )

    got = await q.last_run_at(instance_id=iid, task_type="ping", player_id="p1")
    assert got is not None
    assert abs(got - (now - 10)) < 0.5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_run_at_returns_none_when_no_history(redis_async: object) -> None:
    """Cold start: empty ZSET → caller should treat as "no constraint"."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    got = await q.last_run_at(
        instance_id="bs1", task_type="never_ran", player_id="p1"
    )
    assert got is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_run_at_filters_by_player(redis_async: object) -> None:
    """Same task_type for two players keeps separate histories."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid = "bs1"
    now = time.time()
    await q._append_recent_run(
        instance_id=iid, task_type="claim", player_id="A", now=now - 100
    )
    await q._append_recent_run(
        instance_id=iid, task_type="claim", player_id="B", now=now - 5
    )
    a = await q.last_run_at(instance_id=iid, task_type="claim", player_id="A")
    b = await q.last_run_at(instance_id=iid, task_type="claim", player_id="B")
    assert a is not None and abs(a - (now - 100)) < 0.5
    assert b is not None and abs(b - (now - 5)) < 0.5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_count_cap_enforced(redis_async: object) -> None:
    """More than ``CAP`` rapid appends → ZSET trimmed to CAP (newest survive)."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid = "bs_cap"
    now = time.time()
    # Overflow by 25 so the cap is clearly binding.
    overflow = RECENT_RUNS_RETENTION_CAP + 25
    for i in range(overflow):
        await q._append_recent_run(
            instance_id=iid, task_type="t", player_id="p", now=now - (overflow - i)
        )
    size = await redis_async.zcard(_recent_runs_key(iid))  # type: ignore[attr-defined]
    assert size == RECENT_RUNS_RETENTION_CAP

    # Oldest surviving = oldest among the newest CAP entries — the first 25
    # appends were trimmed. The newest entry has age ≈ 1s; the oldest
    # surviving has age ≈ CAP s.
    age = await q.oldest_recent_run_age(instance_id=iid, now=now)
    assert age is not None
    assert age <= float(RECENT_RUNS_RETENTION_CAP) + 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_oldest_age_none_for_empty_history(redis_async: object) -> None:
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    age = await q.oldest_recent_run_age(instance_id="empty", now=time.time())
    assert age is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_run_survives_recent_runs_count_cap(redis_async: object) -> None:
    """The reported bug: a busy queue evicts a long cron's run from the ZSET.

    A 12h cron fires, then a burst of other tasks overflows the
    ``RECENT_RUNS_RETENTION_CAP``-entry ZSET, evicting the cron's entry. The
    dedicated ``last_run`` marker is NOT subject to the count cap, so
    ``last_run_at`` must still report when the cron last ran — otherwise a
    restart would re-fire the 12h cron immediately.
    """
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid = "bs_busy"
    now = time.time()

    # The long cron fires once, the oldest event of all.
    cron_ts = now - 3600
    await q._append_recent_run(
        instance_id=iid, task_type="long_cron", player_id="p1", now=cron_ts
    )

    # A burst of unrelated work overflows the count cap; the cron's ZSET entry
    # (the oldest by score) is trimmed away.
    overflow = RECENT_RUNS_RETENTION_CAP + 25
    for i in range(overflow):
        await q._append_recent_run(
            instance_id=iid, task_type="busy", player_id="p1", now=now - (overflow - i)
        )

    # Confirm the precondition: the cron is gone from the capped ZSET.
    members = await redis_async.zrangebyscore(  # type: ignore[attr-defined]
        _recent_runs_key(iid), "-inf", "+inf"
    )
    assert not any(
        (m.decode() if isinstance(m, bytes) else str(m)).startswith("long_cron|")
        for m in members
    ), "test precondition: cron entry must have been evicted from recent_runs"

    # The marker survives → cadence is remembered.
    got = await q.last_run_at(instance_id=iid, task_type="long_cron", player_id="p1")
    assert got is not None
    assert abs(got - cron_ts) < 0.5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_run_at_falls_back_to_zset_without_marker(redis_async: object) -> None:
    """Mid-upgrade: a ZSET entry exists but the dedicated marker was never set.

    ``last_run_at`` must fall back to the ZSET scan so an in-flight deployment
    doesn't lose every cron's cadence the moment the new code ships.
    """
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid = "bs_legacy"
    now = time.time()
    await q._append_recent_run(
        instance_id=iid, task_type="legacy", player_id="p1", now=now - 200
    )
    # Simulate a pre-marker world by deleting the dedicated key only.
    await redis_async.delete(_last_run_key(iid, "legacy", "p1"))  # type: ignore[attr-defined]

    got = await q.last_run_at(instance_id=iid, task_type="legacy", player_id="p1")
    assert got is not None
    assert abs(got - (now - 200)) < 0.5
