"""Unit tests for the greedy stamina allocator — the plan's case table.

Pure decision logic: no Redis, no ADB. Each case builds resolved runtime
snapshots and asserts the chosen action + the per-demand verdict trace.
"""
from __future__ import annotations

from games.wos.core.stamina import allocator as alloc
from games.wos.core.stamina.allocator import (
    Decision,
    DemandRuntime,
    SupplyRuntime,
    allocate,
)
from games.wos.core.stamina.model import Demand, Supply

CAP = 200
REGEN = 10.0


def _d(
    did: str,
    priority: int,
    cost: int = 10,
    *,
    active: bool = True,
    quota_used: int = 0,
    daily_quota: int | None = None,
    reserve_floor: int = 0,
    reserve_active: bool | None = None,
) -> DemandRuntime:
    dem = Demand(
        id=did,
        task_type=did,
        priority=priority,
        cost=cost,
        daily_quota=daily_quota,
        reserve_floor=reserve_floor,
    )
    return DemandRuntime(
        demand=dem,
        active=active,
        quota_used=quota_used,
        reserve_active=reserve_active,
    )


def _verdict(decision: Decision, demand_id: str):
    return next(v for v in decision.verdicts if v.demand_id == demand_id)


def _run(est, demands, *, supplies=(), hours=0.0) -> Decision:
    return allocate(
        est,
        demands,
        cap=CAP,
        regen_per_hour=REGEN,
        supplies=supplies,
        hours_to_next_regen=hours,
    )


def test_normal_day_routes_to_intel_then_drains_to_beast():
    # Joe event off → only intel + beast active. Intel (prio 60) wins over beast.
    joe = _d("joe", 100, active=False, daily_quota=30)
    intel = _d("intel", 60, quota_used=7, daily_quota=10)
    beast = _d("beast", 10, daily_quota=None)

    d1 = _run(156, [joe, intel, beast])
    assert d1.action == alloc.CONSUME
    assert d1.target_id == "intel"

    # Once intel's daily quota is full, surplus drains into the beast sink.
    intel_full = _d("intel", 60, quota_used=10, daily_quota=10)
    d2 = _run(156, [joe, intel_full, beast])
    assert d2.action == alloc.CONSUME
    assert d2.target_id == "beast"
    assert _verdict(d2, "intel").reason == alloc.QUOTA_FULL


def test_joe_day_prioritises_bandits_over_intel():
    joe = _d("joe", 100, quota_used=12, daily_quota=30, active=True)
    intel = _d("intel", 60, quota_used=7, daily_quota=10)
    beast = _d("beast", 10, daily_quota=None)

    d = _run(156, [joe, intel, beast])
    assert d.action == alloc.CONSUME
    assert d.target_id == "joe"
    # One consumer per tick: lower-priority demands are simply not considered.
    assert _verdict(d, "intel").reason == alloc.NOT_CONSIDERED
    assert _verdict(d, "beast").reason == alloc.NOT_CONSIDERED


def test_reserve_holds_stamina_for_imminent_joe():
    # Joe window not open yet, but reserve is pre-held (imminent). With only 55
    # stamina, spending 10 would dip below Joe's 50 floor → everything held.
    joe = _d("joe", 100, active=False, reserve_active=True, reserve_floor=50, daily_quota=30)
    intel = _d("intel", 60, quota_used=0, daily_quota=10)
    beast = _d("beast", 10, daily_quota=None)

    d = _run(55, [joe, intel, beast])
    assert d.action == alloc.IDLE
    assert d.reason == "idle_reserve_held"
    assert _verdict(d, "intel").reason == alloc.RESERVE_HELD
    assert _verdict(d, "joe").reason == alloc.WINDOW_CLOSED


def test_overflow_pressure_ignores_reserve():
    # Same as above but projected to overflow before next regen → spend now.
    joe = _d("joe", 100, active=False, reserve_active=True, reserve_floor=50, daily_quota=30)
    intel = _d("intel", 60, quota_used=0, daily_quota=10)
    beast = _d("beast", 10, daily_quota=None)

    d = _run(55, [joe, intel, beast], hours=20.0)  # 55 + 10*20 = 255 > 200
    assert d.overflow_pressure is True
    assert d.action == alloc.CONSUME
    assert d.target_id == "intel"


def test_supply_triggered_when_blocked_by_low_stamina():
    joe = _d("joe", 100, cost=10, active=True, daily_quota=30)
    pet = SupplyRuntime(
        supply=Supply(id="pet", task_type="pet", gives=50, daily_quota=1),
        triggered=True,
        quota_used=0,
    )
    d = _run(5, [joe], supplies=[pet])
    assert d.action == alloc.SUPPLY
    assert d.target_id == "pet"


def test_supply_not_triggered_stays_idle():
    joe = _d("joe", 100, cost=10, active=True, daily_quota=30)
    pet = SupplyRuntime(
        supply=Supply(id="pet", task_type="pet", gives=50, daily_quota=1),
        triggered=False,
        quota_used=0,
    )
    d = _run(5, [joe], supplies=[pet])
    assert d.action == alloc.IDLE
    assert d.reason == "idle_insufficient_no_supply"
    assert _verdict(d, "joe").reason == alloc.INSUFFICIENT


def test_supply_skipped_when_quota_exhausted():
    joe = _d("joe", 100, cost=10, active=True, daily_quota=30)
    pet = SupplyRuntime(
        supply=Supply(id="pet", task_type="pet", gives=50, daily_quota=1),
        triggered=True,
        quota_used=1,  # already used today
    )
    d = _run(5, [joe], supplies=[pet])
    assert d.action == alloc.IDLE


def test_stamina_delta_signs():
    # Consume → negative (spent the cost).
    d = _run(156, [_d("joe", 100, cost=10, active=True, daily_quota=30)])
    assert d.action == alloc.CONSUME
    assert d.stamina_delta == -10

    # Supply → positive (refilled `gives`).
    joe = _d("joe", 100, cost=10, active=True, daily_quota=30)
    pet = SupplyRuntime(
        supply=Supply(id="pet", task_type="pet", gives=50, daily_quota=1),
        triggered=True,
    )
    d2 = _run(5, [joe], supplies=[pet])
    assert d2.action == alloc.SUPPLY
    assert d2.stamina_delta == 50

    # Idle → no change.
    d3 = _run(100, [])
    assert d3.stamina_delta == 0


def test_verdict_trace_is_complete():
    joe = _d("joe", 100, active=True, daily_quota=30)
    intel = _d("intel", 60, daily_quota=10)
    beast = _d("beast", 10, daily_quota=None)
    d = _run(156, [joe, intel, beast])
    assert {v.demand_id for v in d.verdicts} == {"joe", "intel", "beast"}


def test_unlimited_sink_has_quota_and_no_demands_is_idle():
    beast = _d("beast", 10, daily_quota=None, quota_used=9999)
    assert beast.has_quota is True
    assert beast.quota_left is None

    d = _run(100, [])
    assert d.action == alloc.IDLE
    assert d.reason == "idle_no_eligible_demand"


def test_reserves_defaults_to_active():
    on = _d("a", 10, active=True)
    off = _d("b", 10, active=False)
    assert on.reserves is True
    assert off.reserves is False
    # Explicit override wins.
    forced = _d("c", 10, active=False, reserve_active=True)
    assert forced.reserves is True
