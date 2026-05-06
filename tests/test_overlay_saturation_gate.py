"""Saturation gate rejects grey-looking patches that still score high on grayscale NCC."""

from __future__ import annotations

import numpy as np

from analysis.overlay import _apply_min_saturation_gate
from layout.template_match import patch_mean_hsv_saturation


def test_patch_mean_saturation_grey_lower_than_blue() -> None:
    grey = np.full((24, 24, 3), (140, 140, 140), dtype=np.uint8)
    blue = np.full((24, 24, 3), (255, 90, 70), dtype=np.uint8)
    assert patch_mean_hsv_saturation(grey) < patch_mean_hsv_saturation(blue)


def test_saturation_gate_rejects_flat_grey() -> None:
    img = np.zeros((80, 80, 3), dtype=np.uint8)
    img[10:40, 10:70] = (145, 145, 145)
    ok, mean_s, reason = _apply_min_saturation_gate(img, (10, 10), 60, 30, 80.0)
    assert not ok
    assert mean_s is not None
    assert mean_s < 80.0
    assert reason == "low_saturation"


def test_saturation_gate_accepts_saturated_patch() -> None:
    img = np.zeros((80, 80, 3), dtype=np.uint8)
    img[10:40, 10:70] = (255, 100, 80)
    ok, mean_s, reason = _apply_min_saturation_gate(img, (10, 10), 60, 30, 35.0)
    assert ok
    assert mean_s is not None and mean_s >= 35.0
    assert reason is None
