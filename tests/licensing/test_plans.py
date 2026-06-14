"""Plan catalog + tier ladder (R2 free / R3 $5 / R4 $30 + radar)."""
from __future__ import annotations

from licensing.plans import (
    TIER_ORDER,
    external_accounts_limit_for_tier,
    plan_by_id,
    tier_at_least,
    tier_rank,
)


def test_tier_order_is_low_to_high() -> None:
    assert TIER_ORDER == ("r2", "r3", "r4")


def test_r2_is_free() -> None:
    plan = plan_by_id("r2")
    assert plan is not None
    assert plan.price_usd == 0


def test_r3_costs_five_dollars() -> None:
    plan = plan_by_id("r3")
    assert plan is not None
    assert plan.price_usd == 5


def test_r4_costs_thirty_dollars() -> None:
    plan = plan_by_id("r4")
    assert plan is not None
    assert plan.price_usd == 30


def test_unknown_tier_has_no_plan() -> None:
    assert plan_by_id("nope") is None
    assert plan_by_id(None) is None


def test_tier_rank_is_ladder_index() -> None:
    assert tier_rank("r2") == 0
    assert tier_rank("r3") == 1
    assert tier_rank("r4") == 2
    # Case/whitespace tolerant.
    assert tier_rank(" R4 ") == 2


def test_tier_rank_unknown_and_legacy_rank_below_r2() -> None:
    # Legacy / unknown strings rank below the free tier (rank -1).
    for legacy in ("free", "trial", "pro", "nope", None, ""):
        assert tier_rank(legacy) == -1


def test_tier_at_least_ladder() -> None:
    # Higher or equal tier satisfies the minimum.
    assert tier_at_least("r4", "r3") is True
    assert tier_at_least("r3", "r3") is True
    assert tier_at_least("r4", "r4") is True
    assert tier_at_least("r2", "r2") is True
    # Lower tier does not.
    assert tier_at_least("r2", "r3") is False
    assert tier_at_least("r3", "r4") is False


def test_tier_at_least_legacy_unlocks_nothing_paid() -> None:
    # Legacy/unknown tokens rank below r2 → no paid capability.
    for legacy in ("free", "trial", "pro", None):
        assert tier_at_least(legacy, "r3") is False
        assert tier_at_least(legacy, "r4") is False
    # ...but they still aren't "at least r2" either (rank -1 < 0).
    assert tier_at_least("pro", "r2") is False


def test_tier_at_least_invalid_minimum_is_false() -> None:
    assert tier_at_least("r4", "nope") is False
    assert tier_at_least("r4", "") is False


def test_external_account_caps_per_tier() -> None:
    assert external_accounts_limit_for_tier("r2") == 0
    assert external_accounts_limit_for_tier("r3") == 5
    assert external_accounts_limit_for_tier("r4") == 50
    # Unknown / missing tiers fall back to no allowance.
    assert external_accounts_limit_for_tier("nope") == 0
    assert external_accounts_limit_for_tier(None) == 0
