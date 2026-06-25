"""recent_runs (the recent_debuff history) must key on the LOGICAL scenario type,
not the literal ``task_type`` envelope.

Notify / optimizer / calendar pushes all share the generic worker-dispatch shape
``task_type="dsl_scenario"`` with the real key in the ``dsl_scenario`` field. When
``recent_runs`` recorded the literal ``task_type``, every such push collapsed into one
``"dsl_scenario"`` bucket, so unrelated scenarios debuffed each other as one type. These
tests pin that the bucket key is the resolved scenario, so distinct scenarios keep
distinct histories.
"""
from __future__ import annotations

import json
import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue, logical_task_type


def test_logical_task_type_resolves_dsl_scenario_field() -> None:
    # Generic envelope → the real key carried in ``dsl_scenario``.
    assert (
        logical_task_type({"task_type": "dsl_scenario", "dsl_scenario": "claim_mail"})
        == "claim_mail"
    )
    # Cron shape → the literal already IS the scenario key.
    assert logical_task_type({"task_type": "check_main_city"}) == "check_main_city"
    # Degenerate dsl_scenario with no key → literal, unchanged (never silently empty).
    assert logical_task_type({"task_type": "dsl_scenario"}) == "dsl_scenario"


def _push_notify(redis_async, *, iid: str, key: str, player: str, tid: str, ts: float):
    """Mirror ``notify.publisher.enqueue_scenario``: generic envelope, real key in field."""
    body = {
        "task_id": tid,
        "player_id": player,
        "task_type": "dsl_scenario",
        "dsl_scenario": key,
        "priority": 80_000,
        "run_at": ts,
        "instance_id": iid,
        "created_at": ts,
    }
    return redis_async.zadd(f"wos:queue:{iid}", {json.dumps(body): ts})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_records_recent_run_under_logical_scenario(redis_async) -> None:
    """Popping a ``dsl_scenario`` push records recent_runs under its real key, never
    the generic ``"dsl_scenario"`` envelope."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid, player = "bs_logical1", "401227964"
    now = time.time()
    await redis_async.hset(f"wos:instance:{iid}:state", mapping={"active_player": player})
    await _push_notify(redis_async, iid=iid, key="intel_lighthouse", player=player, tid="n1", ts=now)

    item = await q.pop_due(iid, current_screen="main_city")
    assert item is not None and item.dsl_scenario == "intel_lighthouse"

    counts = await q._read_recent_counts(iid, time.time())
    assert counts.get(("intel_lighthouse", player)) == 1
    assert ("dsl_scenario", player) not in counts


@pytest.mark.integration
@pytest.mark.asyncio
async def test_distinct_dsl_scenarios_do_not_share_recent_debuff(redis_async) -> None:
    """A scenario that just ran is debuffed; a DIFFERENT scenario pushed the same way is
    not — they no longer collide in one ``"dsl_scenario"`` recent_runs bucket."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    iid, player = "bs_logical2", "401227964"
    now = time.time()
    await redis_async.hset(f"wos:instance:{iid}:state", mapping={"active_player": player})

    # Run intel_lighthouse once → it accrues recent history under its own key.
    await _push_notify(redis_async, iid=iid, key="intel_lighthouse", player=player, tid="n1", ts=now - 5)
    popped = await q.pop_due(iid, current_screen="main_city")
    assert popped is not None and popped.dsl_scenario == "intel_lighthouse"

    # Now intel_lighthouse (ran) and a different scenario (never ran) are both due.
    await _push_notify(redis_async, iid=iid, key="intel_lighthouse", player=player, tid="n2", ts=now)
    await _push_notify(redis_async, iid=iid, key="claim_mail", player=player, tid="n3", ts=now)

    rows = await q.explain_top_n(iid, current_screen="main_city", n=10)
    by_logical = {r["logical_task_type"]: r for r in rows}
    assert by_logical["intel_lighthouse"]["recent_count"] >= 1   # debuffed by its own run
    assert by_logical["claim_mail"]["recent_count"] == 0         # untouched — separate bucket
