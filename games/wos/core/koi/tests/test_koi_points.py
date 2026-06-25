"""Tests for the King of the Icefield (KoI) points scorer."""
from __future__ import annotations

from games.wos.core.koi import (
    KoiTroopPlanItem,
    days_for,
    points_for,
    score_plan,
    troop_train_points,
)


def test_unit_values_match_source() -> None:
    # Mithril 144k on Days 2/4/5 (the Hero/Combat days), absent elsewhere.
    assert points_for("mithril", 2) == 144000
    assert points_for("mithril", 4) == 144000
    assert points_for("mithril", 5) == 144000
    assert points_for("mithril", 1) is None

    # Chief Gear score 36 on Days 6 & 7 only (KoI uses 36, not SvS's 35).
    assert points_for("chief_gear_score", 6) == 36
    assert points_for("chief_gear_score", 7) == 36
    assert points_for("chief_gear_score", 1) is None

    # Gathering only scores on Day 7, at 3 pts/batch.
    assert points_for("gather_per_batch", 7) == 3
    assert points_for("gather_per_batch", 1) is None

    # Hero shards: rare only appears on Day 7 ("all hero shard types"); Day 2 has mythic+epic.
    assert points_for("hero_shard_rare", 7) == 350
    assert points_for("hero_shard_rare", 2) is None
    assert points_for("hero_shard_mythic", 2) == 3040


def test_days_for_inverted_index() -> None:
    assert days_for("mithril") == (2, 4, 5)
    assert days_for("chief_gear_score") == (6, 7)
    assert days_for("wild_mark_advanced") == (3, 7)
    assert days_for("gather_per_batch") == (7,)
    assert days_for("does_not_exist") == ()


def test_score_plan_totals_and_split() -> None:
    score = score_plan({"2": {"mithril": 2}, "6": {"chief_gear_score": 10}})
    assert score.total == 2 * 144000 + 10 * 36 == 288360
    assert score.per_day == {2: 288000, 6: 360}
    assert score.breakdown[0].activity == "mithril"      # sorted by subtotal desc
    assert score.breakdown[0].subtotal == 288000
    assert score.unknown == ()


def test_score_plan_flags_wrong_day_activity() -> None:
    # Chief gear score does not score on Day 1 → reported, not silently 0.
    score = score_plan({"1": {"chief_gear_score": 5}})
    assert score.total == 0
    assert score.unknown == ("1:chief_gear_score",)
    assert score.breakdown == ()


def test_troop_table_is_empty_unsourced() -> None:
    # KoI troop per-tier points are not sourced → every tier is unknown.
    assert troop_train_points(10) is None
    score = score_plan({}, troops=[KoiTroopPlanItem(action="train", qty=5, tier=10, day=4)])
    assert score.total == 0
    assert score.unknown == ("4:troop_train_t10",)


def test_troop_day_validation() -> None:
    # Troops only score on Days 4 and 6; a Day-3 troop item is flagged.
    score = score_plan(
        {},
        troops=[KoiTroopPlanItem(action="promote", qty=10, from_tier=7, to_tier=8, day=3)],
    )
    assert score.total == 0
    assert score.unknown == ("3:troop_bad_day",)
    # Day 6 is a valid troop day (would score if the tier table were populated).
    score6 = score_plan(
        {},
        troops=[KoiTroopPlanItem(action="promote", qty=10, from_tier=7, to_tier=8, day=6)],
    )
    assert score6.unknown == ("6:troop_promote_t7_t8",)   # valid day, but tier unsourced
