"""Tests for the FC→RFC weekly conversion cost model."""
from __future__ import annotations

from games.wos.core.rfc import (
    convert_path,
    efficient_weekly,
    load_rfc_conversion,
    plan_for_rfc,
)


def test_tier_table_loads() -> None:
    rfc = load_rfc_conversion()
    assert rfc.weekly_cap == 100 and rfc.tier_size == 20
    assert [t.tier for t in rfc.tiers] == [1, 2, 3, 4, 5]
    assert rfc.tier_at(0).fc_cost == 20 and rfc.tier_at(0).expected_rfc == 1.45
    assert rfc.tier_at(19).tier == 1 and rfc.tier_at(20).tier == 2
    assert rfc.tier_at(99).fc_cost == 160
    assert rfc.tier_at(150).tier == 5            # clamps past the cap


def test_convert_path_walks_tiers() -> None:
    # 40 conversions from index 0 → all of Tier 1 (20×20 FC, 20×1.45) + Tier 2 (20×50, 20×2.15).
    p = convert_path(40, 0)
    assert p.conversions == 40
    assert p.fc_spent == 20 * 20 + 20 * 50 == 1400
    assert p.expected_rfc == round(20 * 1.45 + 20 * 2.15, 2) == 72.0
    assert {e["tier"] for e in p.by_tier} == {1, 2}


def test_convert_path_caps_at_weekly_max() -> None:
    p = convert_path(50, start_index=80)         # only 20 left to the cap (80..99)
    assert p.conversions == 20
    assert p.fc_spent == 20 * 160


def test_efficient_weekly_golden_rule() -> None:
    w = efficient_weekly()
    assert w.conversions == 20
    assert w.expected_rfc == 29.0                # 20 × 1.45
    assert w.fc_no_discount == 400               # 20 × 20
    assert w.fc_with_discount == 330             # minus 7 daily 50%-off (7 × 10)


def test_plan_for_rfc_target() -> None:
    # 29 RFC = exactly one efficient week.
    p = plan_for_rfc(29)
    assert p.conversions == 20 and p.weeks == 1
    assert p.fc_needed == 330 and p.expected_rfc == 29.0
    # Without the discount it's the full tier-1 cost.
    assert plan_for_rfc(29, with_discount=False).fc_needed == 400
    # Zero target → zero everything.
    z = plan_for_rfc(0)
    assert z.conversions == 0 and z.weeks == 0 and z.fc_needed == 0


def test_plan_for_rfc_multi_week() -> None:
    p = plan_for_rfc(100)
    assert p.conversions == 69                    # ceil(100 / 1.45) = 69
    assert p.weeks == 4                            # ceil(69 / 20)
    # Discounted Tier-1 ratio (~11 FC/RFC) is below the full-price ratio (400/29 ≈ 13.8).
    assert 10 < p.fc_per_rfc < 13.8
