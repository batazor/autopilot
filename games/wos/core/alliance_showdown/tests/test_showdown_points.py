"""Tests for the Alliance Showdown points scorer + coordinator tilt."""
from __future__ import annotations

from games.wos.core.alliance_showdown import (
    TroopPlanItem,
    points_for,
    score_plan,
    stage_domain_tilt,
    stages_for,
    troop_promote_points,
    troop_train_points,
)


def test_unit_values_match_source() -> None:
    # Mithril is the marquee item — 67.5k on Stages 4 & 6 only.
    assert points_for("mithril", 4) == 67500
    assert points_for("mithril", 6) == 67500
    assert points_for("mithril", 1) is None

    # Refined Fire Crystal: 18,750 on Stages 1/5/6, absent on Stages 2/3 boundaries vary.
    assert points_for("refined_fire_crystal_building", 1) == 18750
    assert points_for("refined_fire_crystal_building", 5) == 18750
    assert points_for("refined_fire_crystal_building", 6) == 18750
    assert points_for("refined_fire_crystal_building", 3) is None

    # Hero shards by rarity (Stage 2 / finale).
    assert points_for("hero_shard_mythic", 2) == 1875
    assert points_for("hero_shard_epic", 2) == 750
    assert points_for("hero_shard_rare", 2) == 210

    # Per-score-point activities.
    assert points_for("chief_charm_score", 3) == 45
    assert points_for("pet_advancement_score", 3) == 30
    assert points_for("chief_gear_score", 5) == 22


def test_stages_for_inverted_index() -> None:
    assert stages_for("mithril") == (4, 6)
    assert stages_for("chief_gear_score") == (5, 6)        # Trade Dominion + finale
    assert stages_for("wild_mark_advanced") == (3, 6)
    assert stages_for("escort_truck") == (1, 2, 3, 4, 5, 6)  # trucks score every stage
    assert stages_for("does_not_exist") == ()


def test_score_plan_totals_and_split() -> None:
    score = score_plan({"4": {"mithril": 3}, "3": {"wild_mark_advanced": 5}})
    assert score.total == 3 * 67500 + 5 * 9370 == 249350
    assert score.per_stage == {3: 46850, 4: 202500}
    # Breakdown is sorted by subtotal desc → Mithril line first.
    assert score.breakdown[0].activity == "mithril"
    assert score.breakdown[0].subtotal == 202500
    assert score.unknown == ()


def test_baldur_bonus_multiplies() -> None:
    # Baldur L6 = +5% × 6 = ×1.30 on Stage 4 lines.
    score = score_plan({"4": {"mithril": 1}}, baldur={4: 6})
    assert score.total == round(67500 * 1.30) == 87750
    # A Baldur level on a stage with no spend does nothing; default (unset) → ×1.0.
    plain = score_plan({"4": {"mithril": 1}})
    assert plain.total == 67500


def test_score_plan_flags_wrong_stage_activity() -> None:
    # Mithril does not score on Stage 1 → reported, not silently counted as 0.
    score = score_plan({"1": {"mithril": 1}})
    assert score.total == 0
    assert score.unknown == ("1:mithril",)
    assert score.breakdown == ()


def test_troop_table_empty_reports_unknown() -> None:
    # The Stage-4 troop panel's per-tier table is unsourced → every tier is unknown.
    assert troop_train_points(10) is None
    assert troop_promote_points(10, 11) is None
    score = score_plan({}, troops=[TroopPlanItem(action="train", qty=5, tier=11, stage=4)])
    assert score.total == 0
    assert score.unknown == ("4:troop_train_t11",)


def test_troop_bad_stage_reported() -> None:
    # Troops only score on Stages 4 and 6.
    score = score_plan({}, troops=[TroopPlanItem(action="train", qty=5, tier=11, stage=2)])
    assert score.unknown == ("2:troop_bad_stage",)


def test_stage_domain_tilt_band_relative() -> None:
    # Stage 4: hero_gear (Mithril 67,500) is the ceiling → full lift; charms (45) ~1.0.
    tilt = stage_domain_tilt(4)
    assert tilt["hero_gear"] == 1.5                        # 1 + AS_TILT_WEIGHT(0.5)
    assert tilt["charms"] < 1.01                           # 1 + 0.5 * 45/67500
    assert "building_progression" not in tilt              # building doesn't score Stage 4
    assert max(tilt.values()) == 1.5

    # Stage 1: building_progression (Refined FC 18,750) is the ceiling.
    s1 = stage_domain_tilt(1)
    assert s1["building_progression"] == 1.5
    assert s1["research"] < 1.05                           # FC shard 625 ≪ 18,750

    # Baldur bonus scales all items equally → tilt is unchanged (scale-invariant).
    assert stage_domain_tilt(4) == tilt


def test_stage_domain_tilt_unknown_stage_empty() -> None:
    assert stage_domain_tilt(99) == {}
