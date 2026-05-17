"""``RedisQueue.pop_due`` must treat ``zrem == 1`` as the actual claim.

Two workers racing on the same instance queue can both ``_collect_ranked_due``
the same top candidate; only one's ``ZREM`` returns 1, the other gets 0 and
needs to fall through to the next ranked item — otherwise the loser returns a
phantom ``QueueItem`` and produces a double execution.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue, _queue_key, _recent_runs_key


def _ghost(
    *,
    task_id: str,
    task_type: str = "who_i_am",
    priority: int = 99_000,
    run_at: float = 0.0,
    instance_id: str = "bs1",
) -> tuple[str, dict[str, Any]]:
    """Synthetic 'race-lost' payload not present in Redis."""
    data: dict[str, Any] = {
        "task_id": task_id,
        "player_id": "",
        "task_type": task_type,
        "priority": priority,
        "run_at": run_at,
        "instance_id": instance_id,
        "created_at": run_at,
    }
    return json.dumps(data), data


def _meta(priority: int) -> dict[str, Any]:
    """Full meta dict matching ``_rank_candidates`` output; ``_log_pop_winner``
    reads every key, so partial dicts break the success-path log call."""
    return {
        "base_priority": priority,
        "effective_priority": priority,
        "graph_debuff": 0,
        "recent_debuff": 0,
        "hops": 0,
        "unreachable_flag": 0,
        "required_node": "",
        "recent_count": 0,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_skips_race_loss_and_claims_next(
    redis_async: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-ranked candidate is missing from Redis (a peer already popped it).

    pop_due must skip it (zrem=0) and claim the second-ranked item instead.
    """
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    now = time.time()
    # Real item — only this one is actually in the sorted set.
    await q.schedule(
        task_id="real",
        player_id="",
        task_type="who_i_am",
        priority=50_000,
        run_at=now - 1,
        instance_id="bs1",
    )
    real_items = await r.zrangebyscore(_queue_key("bs1"), "-inf", "+inf")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert len(real_items) == 1
    real_raw = real_items[0]
    real_data = json.loads(real_raw)

    ghost_raw, ghost_data = _ghost(task_id="ghost", run_at=now - 2)

    async def _fake_collect(*_a: object, **_kw: object) -> list[
        tuple[tuple[int, int, int, float, float], str, dict[str, Any], dict[str, Any]]
    ]:
        # Ghost wins ranking (lower sort key) → tried first → zrem==0 → skip.
        return [
            (
                (-99_000, 0, 0, ghost_data["run_at"], 0.0),
                ghost_raw,
                ghost_data,
                _meta(99_000),
            ),
            (
                (-50_000, 0, 0, real_data["run_at"], 0.0),
                real_raw,
                real_data,
                _meta(50_000),
            ),
        ]

    monkeypatch.setattr(q, "_collect_ranked_due", _fake_collect)

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_id == "real", "must skip the race-lost ghost and claim the real item"

    remaining = await r.zrangebyscore(_queue_key("bs1"), "-inf", "+inf")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert remaining == [], "claimed item must be gone from the queue"

    # ``_append_recent_run`` runs only on a successful claim — recent_runs must
    # have exactly one event tagged with the *real* task_type, never the ghost.
    members = await r.zrangebyscore(_recent_runs_key("bs1"), "-inf", "+inf")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert len(members) == 1
    assert str(members[0]).startswith("who_i_am|"), members


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_returns_none_when_all_candidates_lost(
    redis_async: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ranked candidate has already been popped by a peer → None."""
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    g1_raw, g1_data = _ghost(task_id="g1")
    g2_raw, g2_data = _ghost(task_id="g2", priority=10)

    async def _fake_collect(*_a: object, **_kw: object) -> list[
        tuple[tuple[int, int, int, float, float], str, dict[str, Any], dict[str, Any]]
    ]:
        return [
            ((-99_000, 0, 0, 0.0, 0.0), g1_raw, g1_data, _meta(99_000)),
            ((-10, 0, 0, 0.0, 0.0), g2_raw, g2_data, _meta(10)),
        ]

    monkeypatch.setattr(q, "_collect_ranked_due", _fake_collect)

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is None, "all candidates lost → no claim, no phantom QueueItem"

    members = await r.zrangebyscore(_recent_runs_key("bs1"), "-inf", "+inf")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert members == [], "race-lost candidates must not pollute recent_runs"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_pop_due_yields_each_task_once(redis_async: object) -> None:
    """End-to-end race: many concurrent pop_due calls, one task — claimed once.

    The async client serializes Redis commands so the race is mostly synthetic,
    but the ``ZREM`` claim must still be the boundary that gates the return —
    if two callers ever do read the same top candidate, only one comes back
    with a QueueItem.
    """
    import asyncio

    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    now = time.time()
    n = 5
    for i in range(n):
        await q.schedule(
            task_id=f"t-{i}",
            player_id="",
            task_type="who_i_am",
            priority=80_000 + i,
            run_at=now - 1,
            instance_id="bs1",
        )

    # Twice as many concurrent pops as items so we exercise the "no candidates
    # left" branch too. Each task must be observed exactly once across callers.
    results = await asyncio.gather(
        *(q.pop_due("bs1", current_screen="main_city") for _ in range(n * 2))
    )
    claimed_ids = [item.task_id for item in results if item is not None]
    assert sorted(claimed_ids) == sorted(f"t-{i}" for i in range(n))
    assert results.count(None) == n
