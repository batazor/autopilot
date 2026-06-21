"""Multi-account Arena day plan: serial per-device packing + capacity signals."""
from __future__ import annotations

import pytest
from games.wos.core.arena.reward_window import Tier
from games.wos.core.arena.schedule import (
    ArenaSlot,
    high_return_capacity,
    plan_arena_day,
)


def test_single_device_packs_back_to_back_all_high_return():
    plan = plan_arena_day({"d1": ["a", "b", "c"]}, per_account_minutes=6)
    assert plan.slots == (
        ArenaSlot("a", "d1", 0, 0, 6, Tier.HIGHER),
        ArenaSlot("b", "d1", 1, 6, 12, Tier.HIGHER),
        ArenaSlot("c", "d1", 2, 12, 18, Tier.HIGHER),
    )
    assert plan.capacity_ok()
    assert plan.counts() == {"higher": 3, "normal": 0, "reduced": 0, "missed": 0}
    assert len(plan.on_time()) == 3
    assert plan.bottleneck_device() == "d1"


def test_devices_run_in_parallel_each_from_start():
    plan = plan_arena_day({"d1": ["a", "b"], "d2": ["c"]}, per_account_minutes=10)
    starts = {(s.device_id, s.account_id): s.start_min for s in plan.slots}
    assert starts == {("d1", "a"): 0, ("d1", "b"): 10, ("d2", "c"): 0}  # d2 not queued behind d1


def test_per_account_duration_override():
    plan = plan_arena_day(
        {"d1": ["slow", "fast"]},
        per_account_minutes=5,
        account_minutes={"slow": 30},
    )
    assert [(s.account_id, s.start_min, s.end_min) for s in plan.slots] == [
        ("slow", 0, 30),
        ("fast", 30, 35),
    ]


def test_oversubscribed_tail_degrades_then_misses():
    # Pack from late in the day so the tail crosses NORMAL -> REDUCED -> reset.
    plan = plan_arena_day(
        {"d1": ["w", "x", "y", "z"]},
        per_account_minutes=10,
        start_minute=23 * 60 + 25,  # 23:25 -> 23:25 NORMAL, 23:35/23:45 REDUCED, 23:55 misses reset
    )
    tiers = [s.tier for s in plan.slots]
    assert tiers == [Tier.NORMAL, Tier.REDUCED, Tier.REDUCED, None]
    assert not plan.capacity_ok()
    assert [s.account_id for s in plan.missed()] == ["z"]
    assert plan.counts() == {"higher": 0, "normal": 1, "reduced": 2, "missed": 1}
    assert plan.on_time() == ()


def test_bottleneck_is_the_latest_finishing_device():
    plan = plan_arena_day(
        {"light": ["a"], "heavy": ["b", "c", "d"]},
        per_account_minutes=10,
    )
    assert plan.bottleneck_device() == "heavy"


def test_empty_plan_is_healthy_and_has_no_bottleneck():
    plan = plan_arena_day({})
    assert plan.slots == ()
    assert plan.capacity_ok()
    assert plan.bottleneck_device() is None
    assert plan.counts() == {"higher": 0, "normal": 0, "reduced": 0, "missed": 0}


def test_non_positive_duration_rejected():
    with pytest.raises(ValueError, match="per_account_minutes"):
        plan_arena_day({"d1": ["a"]}, per_account_minutes=0)


# --- headline capacity number ------------------------------------------------

def test_high_return_capacity_counts_accounts_before_22h():
    assert high_return_capacity(60) == 22          # 1320 min / 60
    assert high_return_capacity(60, start_minute=1260) == 1  # only 60 min of higher left
    assert high_return_capacity(2000) == 0          # one account already overflows the window


def test_high_return_capacity_rejects_non_positive():
    with pytest.raises(ValueError, match="per_account_minutes"):
        high_return_capacity(0)
