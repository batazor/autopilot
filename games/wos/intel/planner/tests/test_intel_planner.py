"""Value-greedy Intel batch: colour×kind ranking under a stamina + quota budget."""
from __future__ import annotations

from games.wos.intel.planner import (
    INSUFFICIENT_STAMINA,
    NONE,
    QUOTA_FULL,
    SELECTED,
    SKIP,
    TAKE,
    IntelEvent,
    from_marker,
    intel_value,
    plan_next,
)

# A realistic refresh: 3 gold (one special), 3 purple, 2 blue — like the live screen.
GOLD_HORNED = IntelEvent("skull_horned", "gold", score=0.9, x=360, y=180)
GOLD_FIGHT = IntelEvent("fight", "gold", score=0.8, x=300, y=420)
GOLD_SKULL = IntelEvent("skull", "gold", score=0.8, x=520, y=320)
PURPLE_FIGHT = IntelEvent("fight", "purple", score=0.8, x=260, y=300)
PURPLE_SKULL_A = IntelEvent("skull", "purple", score=0.8, x=540, y=620)
PURPLE_SKULL_B = IntelEvent("skull", "purple", score=0.8, x=560, y=700)
BLUE_SKULL = IntelEvent("skull", "blue", score=0.8, x=120, y=540)
BLUE_FIGHT = IntelEvent("fight", "blue", score=0.8, x=420, y=900)

BOARD = [
    GOLD_FIGHT, PURPLE_SKULL_A, BLUE_SKULL, GOLD_HORNED, PURPLE_FIGHT,
    GOLD_SKULL, BLUE_FIGHT, PURPLE_SKULL_B,
]


# --- value model -------------------------------------------------------------


def test_value_orders_colour_then_kind():
    # gold special > gold ordinary > purple > blue
    assert intel_value(GOLD_HORNED) > intel_value(GOLD_FIGHT)
    assert intel_value(GOLD_FIGHT) > intel_value(PURPLE_FIGHT)
    assert intel_value(PURPLE_FIGHT) > intel_value(BLUE_FIGHT)


def test_from_marker_is_image_free():
    class _Pt:
        x, y = 11, 22

    class _Marker:
        kind, color, score = "camp", "gold", 0.77
        center = _Pt()

    ev = from_marker(_Marker())
    assert (ev.kind, ev.color, ev.score, ev.x, ev.y) == ("camp", "gold", 0.77, 11, 22)


# --- planner -----------------------------------------------------------------


def test_value_greedy_batch_takes_best_first():
    plan = plan_next(BOARD, stamina=56, cost_per_event=10)
    assert plan.reason == SELECTED
    # 56 stamina / 10 = 5 events; highest value first.
    assert plan.total_cost == 50
    # Equal-value pins break ties by screen position (topmost first): the two
    # ordinary gold pins order GOLD_SKULL (y=320) before GOLD_FIGHT (y=420).
    assert [c.event for c in plan.batch] == [
        GOLD_HORNED, GOLD_SKULL, GOLD_FIGHT, PURPLE_FIGHT, PURPLE_SKULL_A,
    ]
    assert plan.step.event is GOLD_HORNED
    # 4 more stamina would unlock a 6th marker.
    assert plan.stamina_short == 4
    assert len(plan.deferred) == 3


def test_reserve_is_held_for_higher_priority_demands():
    # 56 stamina but hold 50 for Crazy Joe → only 6 spendable, nothing affordable.
    plan = plan_next(BOARD, stamina=56, cost_per_event=10, reserve=50)
    assert plan.reason == INSUFFICIENT_STAMINA
    assert plan.batch == ()
    assert plan.reserve == 50
    assert plan.stamina_short == 4          # 4 more stamina clears the reserve+cost


def test_daily_quota_caps_the_batch():
    plan = plan_next(BOARD, stamina=200, cost_per_event=10, daily_quota_left=3)
    assert plan.reason == SELECTED
    assert len(plan.batch) == 3
    assert plan.stamina_short == 0          # blocked by quota, not stamina


def test_quota_full_when_nothing_left_today():
    plan = plan_next(BOARD, stamina=200, cost_per_event=10, daily_quota_left=0)
    assert plan.reason == QUOTA_FULL
    assert plan.batch == ()


def test_insufficient_stamina_defers_everything():
    plan = plan_next(BOARD, stamina=5, cost_per_event=10)
    assert plan.reason == INSUFFICIENT_STAMINA
    assert plan.batch == ()
    assert len(plan.deferred) == len(BOARD)
    assert plan.stamina_short == 5          # 5 → 10 to afford the first


def test_none_when_board_empty():
    assert plan_next([], stamina=100).reason == NONE


def test_priority_only_skips_blue():
    plan = plan_next(BOARD, stamina=200, cost_per_event=10, priority_only=True)
    skipped = [c for c in plan.candidates if c.status == SKIP]
    assert {c.event.color for c in skipped} == {"blue"}
    assert all(c.event.color in ("gold", "purple") for c in plan.batch)
    assert all(c.status == TAKE for c in plan.batch)


def test_min_value_filters_low_loot():
    # Drop everything at/below purple → only the 3 gold survive.
    plan = plan_next(BOARD, stamina=200, cost_per_event=10, min_value=intel_value(PURPLE_FIGHT))
    assert {c.event.color for c in plan.batch} == {"gold"}
