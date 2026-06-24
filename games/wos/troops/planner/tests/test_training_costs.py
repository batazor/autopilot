"""Troop training cost/time table: parse, batch scaling, promotion diff, ETA buff."""
from __future__ import annotations

from games.wos.troops.planner import (
    TrainTier,
    load_training_costs,
    promote_cost_time,
    tier_cost_time,
    train_eta,
)
from games.wos.troops.planner.training_costs import parse_duration

TBL = {
    1: TrainTier(1, {"meat": 100, "wood": 100}, 5),
    2: TrainTier(2, {"meat": 150, "wood": 150, "coal": 20}, 12),
}


def test_parse_duration():
    assert parse_duration("00:00:05") == 5
    assert parse_duration("1d 02:00:00") == 86_400 + 7_200
    assert parse_duration(None) == 0


def test_tier_cost_time_scales_with_batch():
    cost, time_s = tier_cost_time(2, batch=10, table=TBL)
    assert cost == {"meat": 1500, "wood": 1500, "coal": 200}
    assert time_s == 120


def test_missing_tier_is_empty():
    assert tier_cost_time(9, table=TBL) == ({}, 0)


def test_promote_pays_only_the_tier_difference():
    # T2 fresh = meat150/wood150/coal20; T1 = meat100/wood100 → diff meat50/wood50/coal20.
    cost, time_s = promote_cost_time(2, table=TBL)
    assert cost == {"meat": 50, "wood": 50, "coal": 20}
    assert time_s == 12 - 5
    # ...and is cheaper than training T2 fresh.
    fresh, _ = tier_cost_time(2, table=TBL)
    assert sum(cost.values()) < sum(fresh.values())


def test_train_eta_scales_and_speed_buffs_time_only():
    base_t, cost = train_eta(2, 100, table=TBL)
    assert base_t == 12 * 100
    assert cost == {"meat": 15000, "wood": 15000, "coal": 2000}
    fast_t, fast_cost = train_eta(2, 100, speed_pct=100.0, table=TBL)
    assert fast_t == base_t // 2                  # +100% training speed → half the time
    assert fast_cost == cost                      # cost is unaffected by speed


def test_real_stub_loads_empty():
    # The shipped data file is a documented stub (tiers: []) → empty, no error.
    assert load_training_costs() == {}
