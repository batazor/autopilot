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


def test_real_table_loads_all_tiers():
    # The shipped table carries T1..T11, cross-verified against two community
    # calculators (see the YAML header).
    tbl = load_training_costs()
    assert sorted(tbl) == list(range(1, 12))
    # Time is shared across types; spot-check endpoints.
    assert tbl[1].time_s == 12
    assert tbl[11].time_s == 180
    # Cost differs by type: infantry meat-heavy, marksman wood-heavy.
    t11 = tbl[11]
    assert t11.cost_for("infantry") == {"meat": 6970, "wood": 5228, "coal": 1220, "iron": 253}
    assert t11.cost_for("marksman") == {"meat": 4357, "wood": 6448, "coal": 1081, "iron": 349}
    assert t11.cost_for("infantry")["meat"] > t11.cost_for("marksman")["meat"]
    assert t11.cost_for("marksman")["wood"] > t11.cost_for("infantry")["wood"]
    # Base cost (no type given) defaults to the infantry column.
    assert t11.cost == t11.cost_for("infantry")


def test_by_type_cost_selects_column_and_falls_back():
    tbl = {
        2: TrainTier(
            2,
            {"meat": 150},
            12,
            by_type={"lancer": {"meat": 130, "wood": 140}},
        ),
    }
    # Known type → its column; unknown/omitted type → shared base cost.
    assert tier_cost_time(2, troop_type="lancer", table=tbl) == (
        {"meat": 130, "wood": 140},
        12,
    )
    assert tier_cost_time(2, table=tbl) == ({"meat": 150}, 12)
    assert tier_cost_time(2, troop_type="marksman", table=tbl) == ({"meat": 150}, 12)


def test_promote_diff_uses_the_same_type_column():
    tbl = {
        1: TrainTier(1, {}, 5, by_type={"marksman": {"meat": 23, "wood": 34}}),
        2: TrainTier(2, {}, 17, by_type={"marksman": {"meat": 36, "wood": 54}}),
    }
    cost, time_s = promote_cost_time(2, troop_type="marksman", table=tbl)
    assert cost == {"meat": 13, "wood": 20}
    assert time_s == 12
