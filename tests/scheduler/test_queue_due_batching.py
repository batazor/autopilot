"""Due-queue collection uses batched ZRANGEBYSCORE, not one unbounded read."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from config.loader import get_settings
from scheduler.queue import QUEUE_DUE_PARSE_MAX, QUEUE_DUE_ZRANGE_BATCH, RedisQueue

_LOW_PRIORITY = 10_000
_HIGH_PRIORITY = 99_000


def _due_payload(
    *,
    task_id: str,
    priority: int,
    run_at: float,
    task_type: str = "who_i_am",
    instance_id: str = "bs1",
) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "player_id": "",
            "task_type": task_type,
            "priority": priority,
            "run_at": run_at,
            "instance_id": instance_id,
            "created_at": run_at,
        }
    )


def _mock_due_redis(payloads: list[str]) -> AsyncMock:
    """Redis mock that pages ``zrangebyscore`` like the real due-queue iterator."""

    calls: list[tuple[int, int]] = []

    async def _zrangebyscore(
        _key: str,
        _lo: str,
        _hi: str,
        *,
        start: int = 0,
        num: int | None = None,
        withscores: bool = False,
    ) -> list[Any]:
        assert not withscores
        assert num is not None
        calls.append((start, num))
        end = start + num
        return payloads[start:end]

    redis = AsyncMock()
    redis.hget = AsyncMock(return_value="")
    redis.zcount = AsyncMock(return_value=len(payloads))
    redis.zrangebyscore = AsyncMock(side_effect=_zrangebyscore)
    redis.zrange = AsyncMock(return_value=[])
    redis._due_zrange_calls = calls  # type: ignore[attr-defined]
    return redis


@pytest.mark.asyncio
async def test_collect_ranked_due_fetches_due_members_in_batches() -> None:
    now = time.time()
    payloads = [_due_payload(task_id=f"t{i}", priority=90_000 - i, run_at=now - 10) for i in range(5)]

    redis = _mock_due_redis(payloads)
    q = RedisQueue(redis, get_settings())
    ranked = await q._collect_ranked_due("bs1", "main_city", now)

    assert len(ranked) == 5
    assert redis._due_zrange_calls[0] == (0, QUEUE_DUE_ZRANGE_BATCH)  # type: ignore[attr-defined]
    assert redis.zrangebyscore.await_count >= 1


@pytest.mark.asyncio
async def test_collect_ranked_due_ranks_high_priority_after_many_earlier_low_run_at() -> None:
    """Regression: parse cap must not drop later due items before priority ranking.

    Redis returns due members by ``run_at``. With 512 older low-priority tasks and one
    newer high-priority due task (e.g. overlay / identity), ranking must still see
    the urgent item — not only the earliest 512 by ``run_at``.
    """
    now = time.time()
    low_count = QUEUE_DUE_PARSE_MAX
    payloads = [
        _due_payload(
            task_id=f"low-{i}",
            priority=_LOW_PRIORITY,
            run_at=now - 2000 - i,
        )
        for i in range(low_count)
    ]
    payloads.append(
        _due_payload(
            task_id="urgent-overlay",
            priority=_HIGH_PRIORITY,
            run_at=now - 1,
        )
    )

    redis = _mock_due_redis(payloads)
    q = RedisQueue(redis, get_settings())
    ranked = await q._collect_ranked_due("bs1", "main_city", now)

    assert len(ranked) == low_count + 1
    _sort_key, _raw, winner, _meta = ranked[0]
    assert winner["task_id"] == "urgent-overlay"
    assert int(winner["priority"]) == _HIGH_PRIORITY
    assert redis.zrangebyscore.await_count >= (low_count + 1) // QUEUE_DUE_ZRANGE_BATCH


@pytest.mark.asyncio
async def test_collect_ranked_due_skips_queue_fetch_while_loading() -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value="")
    q = RedisQueue(redis, get_settings())

    ranked = await q._collect_ranked_due("bs1", "loading", time.time())

    assert ranked == []
    redis.zcount.assert_not_called()
    redis.zrangebyscore.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_collect_ranked_due_does_not_skip_when_expired_in_first_page(
    redis_async: object,
) -> None:
    """Expired members are removed AFTER the offset-based walk, not during it.

    Regression: with > one page of due items and an expired item in the first
    page, a ZREM mid-iteration shifted every later member back one slot, so the
    next page's ``start=offset`` skipped that many un-parsed (valid) items —
    they silently never ran. Dropping expired members after the pass keeps the
    pagination window stable.
    """
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    now = time.time()
    n_total = QUEUE_DUE_ZRANGE_BATCH + 6  # forces a second offset page
    n_expired = 3  # all in the first page (lowest run_at → lowest ZSET index)

    pipe = r.pipeline(transaction=False)  # type: ignore[attr-defined]
    valid_ids: list[str] = []
    for i in range(n_total):
        run_at = now - 500 + i  # strictly ascending, all due (<= now)
        body: dict[str, Any] = {
            "task_id": f"item-{i}",
            "player_id": "",
            "task_type": "who_i_am",  # device-level → passes the no-active-player gate
            "priority": 50_000,
            "run_at": run_at,
            "instance_id": "bs1",
            "created_at": run_at,
        }
        if i < n_expired:
            body["expires_at"] = now - 1.0  # already expired
        else:
            valid_ids.append(f"item-{i}")
        pipe.zadd("wos:queue:bs1", {json.dumps(body): run_at})
    await pipe.execute()

    ranked = await q._collect_ranked_due("bs1", "main_city", now)
    got_ids = {data["task_id"] for _sk, _raw, data, _meta in ranked}

    # Every non-expired due item must surface — none skipped by the offset shift.
    assert got_ids == set(valid_ids), sorted(set(valid_ids) - got_ids)
    # The 3 expired members were dropped from the ZSET after the walk.
    assert await r.zcard("wos:queue:bs1") == len(valid_ids)  # type: ignore[attr-defined]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_pop_due_preserves_args(redis_async: object) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    now = time.time()

    await q.schedule(
        task_id="generic-tabs",
        player_id="",
        task_type="who_i_am",
        priority=_HIGH_PRIORITY,
        run_at=now - 1,
        instance_id="bs1",
        args={"region": "deals.tabs_strip"},
        skip_if_duplicate=False,
    )

    popped = await q.pop_due("bs1", current_screen="main_city")
    assert popped is not None
    assert popped.args == {"region": "deals.tabs_strip"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_prefers_high_priority_over_512_earlier_low_run_at(
    redis_async: object,
) -> None:
    """End-to-end: ``pop_due`` / ``peek_top_due`` must not starve urgent due tasks."""
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    now = time.time()
    low_count = QUEUE_DUE_PARSE_MAX

    for i in range(low_count):
        await q.schedule(
            task_id=f"low-{i}",
            player_id="",
            task_type="who_i_am",
            priority=_LOW_PRIORITY,
            run_at=now - 2000 - i,
            instance_id="bs1",
            skip_if_duplicate=False,
        )
    await q.schedule(
        task_id="urgent-overlay",
        player_id="",
        task_type="who_i_am",
        priority=_HIGH_PRIORITY,
        run_at=now - 1,
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    peeked = await q.peek_top_due("bs1", current_screen="main_city")
    assert peeked is not None
    assert peeked.task_id == "urgent-overlay"
    assert peeked.priority == _HIGH_PRIORITY

    popped = await q.pop_due("bs1", current_screen="main_city")
    assert popped is not None
    assert popped.task_id == "urgent-overlay"
    assert popped.priority == _HIGH_PRIORITY
