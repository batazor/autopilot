"""Plan catalog: tier → feature mapping (R2 free / R3 $5 / R4 $30 + radar)."""
from __future__ import annotations

from licensing.plans import (
    FEATURE_GIFT_EXTERNAL,
    FEATURE_RADAR,
    features_for_tier,
    plan_by_id,
)


def test_r2_is_free_with_no_features() -> None:
    plan = plan_by_id("r2")
    assert plan is not None
    assert plan.price_usd == 0
    assert features_for_tier("r2") == []


def test_r3_adds_gift_external_for_five_dollars() -> None:
    plan = plan_by_id("r3")
    assert plan is not None
    assert plan.price_usd == 5
    assert features_for_tier("r3") == [FEATURE_GIFT_EXTERNAL]


def test_r4_adds_radar_for_thirty_dollars() -> None:
    plan = plan_by_id("r4")
    assert plan is not None
    assert plan.price_usd == 30
    feats = features_for_tier("r4")
    assert FEATURE_RADAR in feats
    assert FEATURE_GIFT_EXTERNAL in feats  # cumulative


def test_unknown_tier_has_no_features() -> None:
    assert plan_by_id("nope") is None
    assert features_for_tier("nope") == []
    assert features_for_tier(None) == []
