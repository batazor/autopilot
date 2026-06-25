"""Tests for the SvS prep-phase points scorer."""
from __future__ import annotations

from games.wos.core.svs import (
    TroopPlanItem,
    days_for,
    points_for,
    score_plan,
    troop_promote_points,
    troop_train_points,
)


def test_unit_values_match_source() -> None:
    # Mithril is the marquee item — 144k on Days 4 & 5 only.
    assert points_for("mithril", 4) == 144000
    assert points_for("mithril", 5) == 144000
    assert points_for("mithril", 1) is None

    # Refined Fire Crystal: 30k on Days 1/2/5, absent Day 3.
    assert points_for("refined_fire_crystal_building", 1) == 30000
    assert points_for("refined_fire_crystal_building", 2) == 30000
    assert points_for("refined_fire_crystal_building", 5) == 30000
    assert points_for("refined_fire_crystal_building", 3) is None

    # Hero shards by rarity (Day 2 / Day 3).
    assert points_for("hero_shard_mythic", 2) == 3040
    assert points_for("hero_shard_epic", 2) == 1220
    assert points_for("hero_shard_rare", 2) == 350


def test_days_for_inverted_index() -> None:
    assert days_for("polar_terror_rally") == (3,)       # Beast Slay only
    assert days_for("chief_charm_score") == (1, 3, 4)   # charm score across three days
    assert days_for("chief_gear_score") == (5,)         # Power Boost only
    assert days_for("mithril") == (4, 5)
    assert days_for("does_not_exist") == ()


def test_score_plan_totals_and_split() -> None:
    score = score_plan({"4": {"mithril": 3}, "3": {"polar_terror_rally": 5}})
    assert score.total == 3 * 144000 + 5 * 30000 == 582000
    assert score.per_day == {3: 150000, 4: 432000}
    # Breakdown is sorted by subtotal desc → Mithril line first.
    assert score.breakdown[0].activity == "mithril"
    assert score.breakdown[0].subtotal == 432000
    assert score.unknown == ()


def test_score_plan_flags_wrong_day_activity() -> None:
    # Mithril does not score on Day 1 → reported, not silently counted as 0.
    score = score_plan({"1": {"mithril": 1}})
    assert score.total == 0
    assert score.unknown == ("1:mithril",)
    assert score.breakdown == ()


def test_troop_train_and_promote_points() -> None:
    assert troop_train_points(10) == 60
    assert troop_train_points(11) == 75
    assert troop_train_points(12) is None            # partial table → unknown tier
    # Source worked example: promoting T10 → T11 = 75 - 60 = 15 per troop.
    assert troop_promote_points(10, 11) == 15
    assert troop_promote_points(11, 12) is None


def test_score_plan_with_troops() -> None:
    score = score_plan(
        {},
        troops=[TroopPlanItem(action="promote", qty=100, from_tier=10, to_tier=11)],
    )
    assert score.total == 1500                        # 100 * (75 - 60)
    assert score.per_day == {4: 1500}                 # troops score on Day 4
    assert score.breakdown[0].activity == "troop_promote_t10_t11"


def test_score_plan_unknown_troop_tier() -> None:
    score = score_plan({}, troops=[TroopPlanItem(action="train", qty=5, tier=12)])
    assert score.total == 0
    assert score.unknown == ("4:troop_train_t12",)
