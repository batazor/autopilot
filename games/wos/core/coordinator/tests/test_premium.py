"""Premium allocators: speedup routing/timing + currency value-greedy spend."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CurrencySink,
    SpeedupTask,
    allocate_currency,
    recommend_speedups,
)
from games.wos.core.coordinator.premium import (
    SP_CONSTRUCTION,
    SP_GENERAL,
)

HOUR = 3600


def _applied(plan):
    return {(a.task_id, a.speedup_category): a.minutes for a in plan.applies}


def test_type_specific_speedup_used_before_general():
    tasks = [SpeedupTask("build", "construction", 2 * HOUR)]   # 120 min remaining
    inv = {SP_CONSTRUCTION: 60, SP_GENERAL: 200}
    a = _applied(recommend_speedups(tasks, inv))
    assert a[("build", SP_CONSTRUCTION)] == 60                  # spend specific first
    assert a[("build", SP_GENERAL)] == 60                       # general fills the rest


def test_does_not_exceed_remaining_time():
    tasks = [SpeedupTask("build", "construction", 10 * 60)]     # 10 min left
    plan = recommend_speedups(tasks, {SP_CONSTRUCTION: 999})
    assert _applied(plan)[("build", SP_CONSTRUCTION)] == 10     # capped, no waste
    assert plan.leftover[SP_CONSTRUCTION] == 989


def test_longest_task_gets_shared_general_first():
    tasks = [SpeedupTask("short", "research", 30 * 60),
             SpeedupTask("long", "research", 5 * HOUR)]
    # only general available, enough for the long task only
    plan = recommend_speedups(tasks, {SP_GENERAL: 300})
    a = _applied(plan)
    assert ("long", SP_GENERAL) in a                            # longest served first
    assert a[("long", SP_GENERAL)] == 300
    assert ("short", SP_GENERAL) not in a                       # nothing left


def test_construction_speedup_not_applied_to_research_task():
    tasks = [SpeedupTask("res", "research", HOUR)]
    plan = recommend_speedups(tasks, {SP_CONSTRUCTION: 999})
    assert plan.applies == ()                                   # wrong type → can't use


def test_hold_for_points_window_spends_nothing():
    tasks = [SpeedupTask("build", "construction", 5 * HOUR)]
    plan = recommend_speedups(tasks, {SP_CONSTRUCTION: 999}, spend_now=False)
    assert plan.applies == ()
    assert "hold" in plan.reason


# --- currency ----------------------------------------------------------------


def _sink(sid, cost, value, currency="diamonds", available=True):
    return CurrencySink(sid, currency, cost, value, available)


def test_currency_buys_highest_value_within_balance():
    sinks = [_sink("queue", 4000, 90), _sink("recruit", 2000, 60), _sink("refresh", 1000, 30)]
    plan = allocate_currency(5000, sinks, currency="diamonds")
    # highest value first: queue (4000) bought; then 1000 left → recruit (2000) no, refresh (1000) yes
    assert plan.spend == ("queue", "refresh")
    assert plan.remaining == 0
    assert dict(plan.skipped)["recruit"] == "insufficient"


def test_currency_skips_unavailable_and_unaffordable():
    sinks = [_sink("queue", 4000, 90, available=False), _sink("recruit", 6000, 60)]
    plan = allocate_currency(5000, sinks, currency="diamonds")
    assert plan.spend == ()
    reasons = dict(plan.skipped)
    assert reasons["queue"] == "unavailable"
    assert reasons["recruit"] == "insufficient"


def test_currency_filters_by_type():
    sinks = [_sink("gem_pack", 100, 50, currency="diamonds"),
             _sink("frost_pack", 100, 99, currency="frost_star")]
    plan = allocate_currency(1000, sinks, currency="frost_star")
    assert plan.spend == ("frost_pack",)                        # only frost-star sinks
