"""Target stamina-cost gate: abort the operation when the View cost digit is RED.

The View popup paints the Attack/Explore/Rescue cost digit RED when the account
can't afford it. The button itself is orange/green (orange's hue bleeds into
"red"), so the reliable signal is the WHITE digit — present (~26%) when
affordable, ~0 when the digit is red. These tests pin the pure decision logic.
"""
from __future__ import annotations

import numpy as np
from games.wos.intel.exec import (
    _COST_WHITE_MIN,
    decide_target_affordable,
    white_fraction,
)


def test_white_fraction_none_on_empty() -> None:
    assert white_fraction(None) is None
    assert white_fraction(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_white_fraction_detects_white_digit() -> None:
    # A patch that is mostly saturated red with a white digit stroke.
    patch = np.zeros((40, 60, 3), dtype=np.uint8)
    patch[:, :, 2] = 210  # red background
    patch[10:30, 20:40] = (240, 240, 240)  # white strokes (BGR)
    frac = white_fraction(patch)
    assert frac is not None and frac > 0.1


def test_white_fraction_near_zero_for_all_red() -> None:
    red = np.zeros((40, 60, 3), dtype=np.uint8)
    red[:, :, 2] = 210
    red[10:30, 20:40] = (30, 30, 220)  # saturated red digit
    frac = white_fraction(red)
    assert frac is not None and frac < 0.02


def test_decide_affordable_when_white_present() -> None:
    # Live-calibrated affordable baseline: ~26% white on both button colours.
    assert decide_target_affordable(0.256) == "yes"
    assert decide_target_affordable(0.12) == "yes"       # 1-digit affordable
    assert decide_target_affordable(_COST_WHITE_MIN) == "yes"


def test_decide_insufficient_when_digit_not_white() -> None:
    # Red digit → white share collapses → abort.
    assert decide_target_affordable(0.0) == "no"
    assert decide_target_affordable(0.02) == "no"


def test_decide_fail_open_on_unreadable() -> None:
    # A capture glitch must never abort an otherwise-good target.
    assert decide_target_affordable(None) == "yes"
