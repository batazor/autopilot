"""Tests for the pure Fishing Tournament decision engine (fish_engine)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from api.services.fish_common import FishDetectionRow
from api.services.fish_engine import (
    _nearest_index,
    decide_phase,
    find_hook,
    hook_zone_direction,
    level_trend,
    parse_level,
    plan_action,
    plan_dodge,
    plan_swipe,
    resolve_phase,
    track_fish,
)


def _row(cx: int, cy: int, *, w: int = 40, h: int = 30) -> FishDetectionRow:
    return FishDetectionRow(
        x=cx - w // 2, y=cy - h // 2, width=w, height=h,
        center_x=cx, center_y=cy, confidence=0.9, class_name="fish",
    )


# --- parse_level -------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14/100", (14, 100)),
        ("  7 / 100 ", (7, 100)),
        ("lvl 14/100 done", (14, 100)),
        ("100/100", (100, 100)),
        ("0/100", (0, 100)),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_level(text: str | None, expected: tuple[int, int] | None) -> None:
    assert parse_level(text) == expected


# --- level_trend / decide_phase ----------------------------------------------
@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        ([], "flat"),
        ([14], "flat"),
        ([14, 16], "up"),
        ([16, 14], "down"),
        ([14, 14, 14], "flat"),
        ([10, 12, 14], "up"),
        ([20, 18, 16], "down"),
        ([14, 16, 16, 16], "flat"),  # window=3 sees the plateau, not the old rise
    ],
)
def test_level_trend(levels: list[int], expected: str) -> None:
    assert level_trend(levels) == expected


def test_level_trend_min_delta() -> None:
    assert level_trend([14, 15], min_delta=2) == "flat"
    assert level_trend([14, 16], min_delta=2) == "up"


def test_decide_phase_follows_direction() -> None:
    assert decide_phase([14, 16]) == "collect"   # climbing → набор высоты
    assert decide_phase([16, 14]) == "dodge"      # falling
    assert decide_phase([14, 14]) == "dodge"      # flat
    assert decide_phase([14, 16, 16, 16]) == "dodge"  # climb stopped → revert


def test_resolve_phase_shield_forces_dodge() -> None:
    # Shield up ⇒ descending ("идём вниз") ⇒ dodge even while the counter climbs.
    assert resolve_phase([14, 16], protected=True) == "dodge"
    # Shield down, no hook signal ⇒ fall back to the altitude direction.
    assert resolve_phase([14, 16], protected=False) == "collect"
    assert resolve_phase([16, 14], protected=False) == "dodge"
    assert resolve_phase([14, 14], protected=False) == "dodge"


def test_hook_zone_direction() -> None:
    h = 1280
    assert hook_zone_direction(int(0.15 * h), h) == "down"   # high on screen
    assert hook_zone_direction(int(0.85 * h), h) == "up"     # low on screen
    assert hook_zone_direction(int(0.50 * h), h) is None     # ambiguous middle
    assert hook_zone_direction(None, h) is None
    assert hook_zone_direction(100, 0) is None


def test_resolve_phase_hook_position_is_primary() -> None:
    # Hook high ⇒ descending ⇒ dodge, even though the counter is climbing.
    assert resolve_phase([14, 16], hook_direction="down") == "dodge"
    # Hook low ⇒ ascending ⇒ collect, even though the counter is falling.
    assert resolve_phase([16, 14], hook_direction="up") == "collect"
    # No hook signal ⇒ fall back to the altitude direction.
    assert resolve_phase([14, 16], hook_direction=None) == "collect"


# --- find_hook (delegates to the hook_detect module) -------------------------
# The locator is calibrated for the real water scene (the glow ring shares the
# water's hue, so it's separated by brightness, not colour) — exercise it against
# the captured reference frame, not a synthetic pure-cyan blob.
_GAMEPLAY_FRAME = (
    Path(__file__).resolve().parents[1]
    / "games/wos/events/fishing_tournament/references/gameplay.png"
)


def test_find_hook_locates_ring_on_real_frame() -> None:
    import cv2

    frame = cv2.imread(str(_GAMEPLAY_FRAME))
    assert frame is not None, f"missing reference frame {_GAMEPLAY_FRAME}"
    hook = find_hook(frame)
    assert hook is not None
    cx, cy = hook
    assert abs(cx - 352) <= 15  # blue shield ring centre / hook column
    assert abs(cy - 194) <= 15


def test_find_hook_none_when_absent() -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)  # no ring, no green node
    assert find_hook(frame) is None
    assert find_hook(None) is None  # type: ignore[arg-type]
    assert find_hook(np.zeros((0, 0, 3), dtype=np.uint8)) is None


# --- track_fish (velocity + lead) --------------------------------------------
def test_track_fish_measures_velocity() -> None:
    prev = [_row(100, 100)]
    cur = [_row(140, 100)]  # +40px in 0.5s → 80 px/s east
    [t] = track_fish(prev, cur, dt_s=0.5)
    assert t["tracked"] is True
    assert t["vx"] == pytest.approx(80.0)
    assert t["vy"] == pytest.approx(0.0)
    assert (t["lead_x"], t["lead_y"]) == (140, 100)  # lead_s=0 → at the centre


def test_track_fish_leads_position() -> None:
    [t] = track_fish([_row(100, 100)], [_row(140, 100)], dt_s=0.5, lead_s=0.25)
    assert t["lead_x"] == 160  # 140 + 80*0.25
    assert t["lead_y"] == 100


def test_track_fish_unmatched_has_no_velocity() -> None:
    # No previous frame → cannot measure velocity, lead stays at the centre.
    [t] = track_fish([], [_row(140, 100)], dt_s=0.5, lead_s=1.0)
    assert t["tracked"] is False
    assert t["vx"] == 0.0
    assert (t["lead_x"], t["lead_y"]) == (140, 100)


def test_track_fish_ignores_far_jump() -> None:
    [t] = track_fish([_row(50, 50)], [_row(700, 700)], dt_s=0.5)  # > match dist
    assert t["tracked"] is False
    assert t["vx"] == 0.0


def test_track_fish_no_dt() -> None:
    [t] = track_fish([_row(100, 100)], [_row(140, 100)], dt_s=None, lead_s=1.0)
    assert t["tracked"] is False
    assert (t["lead_x"], t["lead_y"]) == (140, 100)


def test_track_fish_clamps_lead_on_frame() -> None:
    # Fast eastward fish near the right edge → lead clamped inside the frame.
    [t] = track_fish([_row(650, 100)], [_row(700, 100)], dt_s=0.1, lead_s=1.0)
    assert t["vx"] == pytest.approx(500.0)
    assert t["lead_x"] == 719  # clamped to frame_w - 1


# --- _nearest_index ----------------------------------------------------------
def test_nearest_index() -> None:
    assert _nearest_index([(100, 500), (400, 250)], (360, 195)) == 1
    assert _nearest_index([], (360, 195)) == -1


# --- plan_swipe: collect -----------------------------------------------------
def test_plan_swipe_collect_steers_toward_target() -> None:
    plan = plan_swipe((360, 195), 440, "collect")
    assert plan is not None
    assert plan["direction"] == "right"
    assert plan["to_x"] > 360
    assert plan["from_y"] == plan["to_y"] == 195  # horizontal only


def test_plan_swipe_collect_left() -> None:
    plan = plan_swipe((360, 195), 120, "collect")
    assert plan is not None
    assert plan["direction"] == "left"
    assert plan["to_x"] < 360


def test_plan_swipe_collect_deadzone_holds() -> None:
    assert plan_swipe((360, 195), 372, "collect") is None


# --- plan_swipe: dodge -------------------------------------------------------
def test_plan_swipe_dodge_steers_away() -> None:
    plan = plan_swipe((360, 195), 400, "dodge")  # fish to the right & close
    assert plan is not None
    assert plan["direction"] == "left"


def test_plan_swipe_dodge_holds_when_fish_far() -> None:
    assert plan_swipe((360, 195), 600, "dodge") is None


def test_plan_swipe_dodge_centered_flees_to_more_room() -> None:
    plan = plan_swipe((300, 195), 300, "dodge")  # left-of-middle hook → flee right
    assert plan is not None
    assert plan["direction"] == "right"


# --- plan_action (orchestration) ---------------------------------------------
def test_plan_action_collect_end_to_end() -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    import cv2

    cv2.circle(frame, (360, 150), 18, (255, 255, 0), -1)  # hook
    dets = [_row(300, 400)]  # fish left of the hook
    plan = plan_action(frame, dets, [14, 16])  # altitude climbing → collect
    assert plan["phase"] == "collect"
    assert plan["level_trend"] == "up"
    assert plan["hook_x"] is not None and abs(plan["hook_x"] - 360) <= 4
    assert plan["target_index"] == 0
    assert plan["swipe"] is not None
    assert plan["swipe"]["direction"] == "left"


def test_plan_action_leads_moving_target() -> None:
    # Fish sits within the deadzone now but is moving right fast; with a lead the
    # engine aims ahead and commits to a right swipe instead of holding.
    prev = [_row(300, 400)]
    cur = [_row(340, 400)]  # +40px in 0.5s → 80 px/s east
    hold = plan_action(
        None, cur, [14, 16], prev_detections=prev, dt_s=0.5, lead_s=0.0,
        fallback_hook=(360, 195),
    )
    assert hold["swipe"] is None  # 340 is within the collect deadzone of 360

    lead = plan_action(
        None, cur, [14, 16], prev_detections=prev, dt_s=0.5, lead_s=1.0,
        fallback_hook=(360, 195),
    )
    assert lead["target_lead_x"] == 420  # 340 + 80*1.0
    assert lead["swipe"] is not None
    assert lead["swipe"]["direction"] == "right"


def test_plan_action_interception_aims_further_than_no_latency() -> None:
    # A fish moving right; the interception model (fish velocity + the hook's own
    # travel time over the action latency) aims further ahead than the no-latency
    # baseline — so the hook meets the body, not the trailing tail.
    prev = [_row(300, 400)]
    cur = [_row(360, 400)]  # +60px in 0.3s → 200 px/s east
    rising = [10, 12, 14]  # altitude climbing → collect (interception aim applies)
    base = plan_action(
        None, cur, rising, prev_detections=prev, dt_s=0.3, fallback_hook=(360, 195),
    )
    intercept = plan_action(
        None, cur, rising, prev_detections=prev, dt_s=0.3,
        base_latency_s=0.5, hook_speed_px_s=1400.0, fallback_hook=(360, 195),
    )
    # No latency/speed → aim at the fish's current x (unchanged behaviour).
    assert base["target_lead_x"] == 360
    # With latency + travel time, aim well ahead in the fish's direction.
    assert intercept["target_lead_x"] > 460


def test_plan_dodge_flees_to_emptier_side() -> None:
    # Hook between two fish at its depth: one close on the right, one far on the
    # left → the field flees LEFT (toward the bigger gap), not into the right fish.
    hook = (360, 200)
    tracked = track_fish(
        [], [_row(420, 210), _row(180, 210)], dt_s=None,
    )  # both at the hook's depth; right one (420) is closer than left (180)
    swipe = plan_dodge(hook, tracked)
    assert swipe is not None
    assert swipe["direction"] == "left"  # away from the nearer (right) fish


def test_plan_dodge_holds_in_open_water() -> None:
    # No fish near the hook's depth/lane → no dodge.
    hook = (360, 200)
    tracked = track_fish([], [_row(360, 900)], dt_s=None)  # far below → not a threat
    assert plan_dodge(hook, tracked) is None


def test_track_fish_ema_blends_with_prior_velocity() -> None:
    # Prior velocity 100 px/s east; this frame the fish is momentarily still.
    # EMA(α=0.5) reports the blend (50), not the raw single-frame 0 — steadier lead.
    prior = track_fish([_row(200, 400)], [_row(300, 400)], dt_s=1.0)  # vx≈100
    assert prior[0]["vx"] == 100.0
    smoothed = track_fish(
        [_row(300, 400)], [_row(300, 400)], dt_s=1.0,
        prev_tracked=prior, vel_ema_alpha=0.5,
    )
    assert smoothed[0]["vx"] == 50.0  # 0.5*0 + 0.5*100


def test_track_fish_rejects_implausible_match() -> None:
    # A match within the distance gate but at an absurd implied speed (1500 px/s)
    # is an ID swap, not motion — rejected (no wild velocity).
    res = track_fish([_row(300, 400)], [_row(450, 400)], dt_s=0.1)  # 150px/0.1s
    assert res[0]["tracked"] is False
    assert res[0]["vx"] == 0.0


def test_plan_action_dodge_uses_fallback_hook() -> None:
    dets = [_row(360, 220)]
    plan = plan_action(None, dets, [16, 14], fallback_hook=(360, 195))  # falling → dodge
    assert plan["phase"] == "dodge"
    assert plan["hook_x"] == 360
    assert plan["swipe"] is not None
    assert plan["swipe"]["phase"] == "dodge"


def test_plan_action_no_detections() -> None:
    plan = plan_action(None, [], [14, 16], fallback_hook=(360, 195))
    assert plan["swipe"] is None
    assert plan["target_index"] == -1
    assert plan["tracked"] == []
    assert plan["protected"] is None  # no frame → shield state unknown


def test_plan_action_reports_protected_shield_on_real_frame() -> None:
    import cv2

    frame = cv2.imread(str(_GAMEPLAY_FRAME))
    assert frame is not None
    plan = plan_action(frame, [], [14, 16])
    # The reference frame shows the blue protection ring around the hook.
    assert plan["protected"] is True
    assert plan["hook_x"] is not None and abs(plan["hook_x"] - 352) <= 15
    # Hook sits high on screen ⇒ descending; counter is climbing, but the
    # descent (hook position + shield) overrides → dodge.
    assert plan["hook_direction"] == "down"
    assert plan["level_trend"] == "up"
    assert plan["phase"] == "dodge"
