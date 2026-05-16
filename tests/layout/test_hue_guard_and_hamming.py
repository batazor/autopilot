"""``optional_min_match_saturation`` YAML parsing (hue_guard / max_hamming_bits removed)."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.overlay import _apply_min_saturation_gate
from analysis.overlay_rules import optional_min_match_saturation
from layout.template_match import patch_mean_hsv_saturation


def test_optional_min_match_saturation_parses_float() -> None:
    assert optional_min_match_saturation({"min_match_saturation": 48}) == pytest.approx(48.0)


def test_optional_min_match_saturation_missing_or_bool_is_none() -> None:
    assert optional_min_match_saturation({}) is None
    assert optional_min_match_saturation({"min_match_saturation": True}) is None
    assert optional_min_match_saturation({"min_match_saturation": False}) is None


def test_optional_min_match_saturation_rejects_invalid() -> None:
    assert optional_min_match_saturation({"min_match_saturation": "nope"}) is None


def _solid_patch(bgr: tuple[int, int, int], size: int = 16) -> np.ndarray:
    p = np.zeros((size, size, 3), dtype=np.uint8)
    p[..., 0] = bgr[0]
    p[..., 1] = bgr[1]
    p[..., 2] = bgr[2]
    return p


def test_patch_mean_saturation_red_is_high() -> None:
    assert patch_mean_hsv_saturation(_solid_patch((0, 0, 255))) > 200.0


def test_patch_mean_saturation_gray_is_low() -> None:
    assert patch_mean_hsv_saturation(_solid_patch((128, 128, 128))) < 5.0


def test_min_match_saturation_gate_rejects_gray_in_image() -> None:
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    img[:, :] = (128, 128, 128)
    ok, mean_s, reason = _apply_min_saturation_gate(img, (0, 0), 16, 16, 40.0)
    assert not ok
    assert reason == "low_saturation"
    assert mean_s is not None and mean_s < 5.0


def test_min_match_saturation_gate_accepts_saturated_in_image() -> None:
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    img[:, :] = (0, 0, 255)
    ok, mean_s, reason = _apply_min_saturation_gate(img, (0, 0), 16, 16, 40.0)
    assert ok
    assert reason is None
    assert mean_s is not None and mean_s > 200.0
