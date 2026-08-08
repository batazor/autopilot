"""Multi-march chaining: ``queue_next_intel_run`` re-enqueues while slots allow.

The exec sits at the end of both dispatch branches of ``intel_run`` — it must
push a follow-up run only when the march-slot view (capacity fact / default −
occupancy − active leases) leaves a free slot AND the last stamina read covers
another event. Termination relies on a non-dispatching run never reaching the
exec, so these tests also pin the skip reasons.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from games.wos.intel import chain

from tasks.dsl_exec.context import DslExecContext


class _FakeRedis:
    def __init__(self, hashes: dict[str, dict[str, Any]] | None = None) -> None:
        self.hashes = hashes or {}
        self.strings: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        h = self.hashes.get(key, {})
        for f in fields:
            h.pop(f, None)
        return len(fields)


def _ctx(redis: _FakeRedis, **args: Any) -> DslExecContext:
    return DslExecContext(
        instance_id="bs5",
        player_id="p1",
        redis_client=redis,  # type: ignore[arg-type]
        args=args,
        result={},
    )


def _lease_entry(*, slots: int = 1, ttl: int = 300) -> str:
    now = time.time()
    return json.dumps(
        {
            "slots": slots,
            "status": "confirmed",
            "lease_end": now + ttl,
            "confirm_by": now + ttl,
        }
    )


@pytest.mark.asyncio
async def test_chains_when_slot_and_stamina_remain(monkeypatch) -> None:
    redis = _FakeRedis({"wos:player:p1:state": {"stamina": "25"}})
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue
    )
    ctx = _ctx(redis)

    await chain.queue_next_intel_run(ctx)

    assert ctx.result["action"] == "queued"
    assert len(pushed) == 1
    assert pushed[0]["scenario"] == "intel_run"
    assert pushed[0]["skip_if_duplicate"] is False
    # capacity default 2, nothing occupied/held → 2 free
    assert ctx.result["free_slots"] == 2


@pytest.mark.asyncio
async def test_skips_when_leases_fill_capacity(monkeypatch) -> None:
    redis = _FakeRedis(
        {
            "wos:player:p1:state": {"stamina": "25"},
            "wos:player:p1:resources:reservations": {
                "a": _lease_entry(),
                "b": _lease_entry(),
            },
        }
    )
    # Point the ledger key helper at our fake hash regardless of naming drift.
    from games.wos.core.resources import adapter as resource_adapter

    monkeypatch.setattr(
        resource_adapter,
        "_ledger_key",
        lambda player_id: f"wos:player:{player_id}:resources:reservations",
    )

    async def _fake_enqueue(**kwargs: Any) -> bool:
        return True

    monkeypatch.setattr("tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue)
    ctx = _ctx(redis)

    await chain.queue_next_intel_run(ctx)

    # Zero free slots no longer ends the chain: camp pins dispatch without a
    # march queue (operator-confirmed), so the next pass still runs — as a
    # camp-only window (the tap-gate restricts kinds when it sees no slots).
    assert ctx.result["action"] == "queued"
    assert ctx.result["chain_window"] == "camp_only"
    assert ctx.result["held_slots"] == 2


@pytest.mark.asyncio
async def test_skips_on_insufficient_stamina() -> None:
    redis = _FakeRedis({"wos:player:p1:state": {"stamina": "4"}})
    ctx = _ctx(redis)

    await chain.queue_next_intel_run(ctx)

    assert ctx.result["action"] == "skipped"
    assert ctx.result["reason"] == "insufficient_stamina"


@pytest.mark.asyncio
async def test_capacity_fact_overrides_default(monkeypatch) -> None:
    redis = _FakeRedis(
        {
            "wos:player:p1:state": {
                "stamina": "25",
                "marches.capacity": "1",
                "marches.active_count": "1",
            }
        }
    )

    async def _fake_enqueue(**kwargs: Any) -> bool:
        return True

    monkeypatch.setattr("tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue)
    ctx = _ctx(redis)

    await chain.queue_next_intel_run(ctx)

    # Capacity fact honoured (1 slot, occupied) → camp-only chain window.
    assert ctx.result["action"] == "queued"
    assert ctx.result["chain_window"] == "camp_only"
    assert ctx.result["capacity"] == 1


@pytest.mark.asyncio
async def test_unknown_stamina_still_chains(monkeypatch) -> None:
    """A failed stamina read must not block the chain — the follow-up run
    re-reads it on the board and its planner makes the real call."""
    redis = _FakeRedis({"wos:player:p1:state": {}})
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue
    )
    ctx = _ctx(redis)

    await chain.queue_next_intel_run(ctx)

    assert ctx.result["action"] == "queued"
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_chain_ends_on_exhausted_board_snapshot(monkeypatch) -> None:
    """A fresh board snapshot with zero viable pins ends the chain without the
    terminating wasted visit (no re-enqueue)."""
    redis = _FakeRedis({"wos:player:p1:state": {"stamina": "25"}})
    redis.strings["wos:player:p1:intel:board"] = json.dumps(
        {"viable_left": 0, "detected": 2, "captured_at": time.time()}
    )
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue
    )

    ctx = _ctx(redis)
    await chain.queue_next_intel_run(ctx)

    assert pushed == []
    assert ctx.result["action"] == "skipped"
    assert ctx.result["reason"] == "board_exhausted"


@pytest.mark.asyncio
async def test_chain_still_runs_with_viable_board_snapshot(monkeypatch) -> None:
    redis = _FakeRedis({"wos:player:p1:state": {"stamina": "25"}})
    redis.strings["wos:player:p1:intel:board"] = json.dumps(
        {"viable_left": 3, "detected": 4, "captured_at": time.time()}
    )
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue
    )

    await chain.queue_next_intel_run(_ctx(redis))

    assert len(pushed) == 1


# --- maybe_chain_after_tap: the robust, tap-time chain -----------------------


@pytest.mark.asyncio
async def test_chain_after_tap_enqueues_when_board_and_stamina_allow(monkeypatch) -> None:
    redis = _FakeRedis()
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr("tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue)

    ok = await chain.maybe_chain_after_tap(_ctx(redis), board_left=3, stamina=50.0, cost=10)

    assert ok is True
    assert len(pushed) == 1
    assert pushed[0]["scenario"] == "intel_run"


@pytest.mark.asyncio
async def test_chain_after_tap_stops_on_empty_board(monkeypatch) -> None:
    # board_left == 0 → the sweep is done; do not chain another no-op pass.
    redis = _FakeRedis()
    pushed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario",
        lambda **k: pushed.append(k) or True,
    )

    ok = await chain.maybe_chain_after_tap(_ctx(redis), board_left=0, stamina=50.0, cost=10)

    assert ok is False
    assert pushed == []


@pytest.mark.asyncio
async def test_chain_after_tap_stops_when_stamina_below_cost(monkeypatch) -> None:
    redis = _FakeRedis()
    pushed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._enqueue_scenario",
        lambda **k: pushed.append(k) or True,
    )

    ok = await chain.maybe_chain_after_tap(_ctx(redis), board_left=2, stamina=4.0, cost=10)

    assert ok is False
    assert pushed == []


@pytest.mark.asyncio
async def test_chain_after_tap_chains_when_stamina_unknown(monkeypatch) -> None:
    # Unknown stamina (read failed) must not stop the sweep — the next run
    # re-reads it on the board and its planner makes the real call.
    redis = _FakeRedis()
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr("tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue)

    ok = await chain.maybe_chain_after_tap(_ctx(redis), board_left=2, stamina=None, cost=10)

    assert ok is True
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_schedule_reward_claim_enqueues_delayed_pickup(monkeypatch) -> None:
    redis = _FakeRedis()
    pushed: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> bool:
        pushed.append(kwargs)
        return True

    monkeypatch.setattr("tasks.dsl_scenario_helpers._enqueue_scenario", _fake_enqueue)

    now = time.time()
    ok = await chain.schedule_reward_claim(_ctx(redis), delay=40.0)

    assert ok is True
    assert len(pushed) == 1
    assert pushed[0]["scenario"] == "intel_claim_reward"
    assert pushed[0]["skip_if_duplicate"] is True
    assert pushed[0]["run_at"] >= now + 39  # ~40s out


@pytest.mark.asyncio
async def test_schedule_reward_claim_noop_without_player() -> None:
    ctx = DslExecContext(
        instance_id="bs5", player_id="", redis_client=_FakeRedis(), args={}, result={}
    )
    assert await chain.schedule_reward_claim(ctx) is False
