"""Peak-uniqueness gate (Lowe-style ratio test) for sliding ``findIcon`` matches.

The gate catches a class of false positives the existing color/edge/saturation gates miss:
**low-information templates** (smooth gradients, mostly flat patches) reach high NCC by
correlating with any visually similar region of the screen — and produce a *plateau* of
near-equal peaks across the heatmap. We reject when the best peak is not meaningfully
better than the next-best peak in a structurally different ROI location.

True positives observed on real frames keep ≥0.12 margin; false positives plateau at ≤0.05.
The default gate (``0.08``) sits between the two; YAML ``peak_unique_margin`` overrides it.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from analysis.overlay import _apply_peak_uniqueness_gate, evaluate_overlay_rules, load_analyze_yaml
from analysis.overlay_rules import DEFAULT_PEAK_UNIQUE_MARGIN, optional_peak_unique_margin
from layout.template_match import match_template_in_search_roi_bbox_percent

REPO = Path(__file__).resolve().parents[1]
_FIXTURE_HP_SCREEN = REPO / "tests" / "fixtures" / "hand_pointer_small_screen.png"
_FIXTURE_MAIN_CITY = REPO / "tests" / "fixtures" / "main_city_current_state.png"
_HP_REF = REPO / "references" / "hand_pointer_small.png"
_HP_CROP = REPO / "references" / "crop" / "hand_pointer_small_hand_pointer_small.png"
_AREA = REPO / "area.json"
_ANALYZE = REPO / "analyze" / "analyze.yaml"

_DEPS = all(p.is_file() for p in (_HP_REF, _HP_CROP, _AREA, _ANALYZE))


# --------------------------------------------------------------------------------------
# Pure-function tests for the gate (no fixtures required)
# --------------------------------------------------------------------------------------


def test_peak_uniqueness_gate_passes_when_margin_above_required() -> None:
    ok, margin, reason = _apply_peak_uniqueness_gate(0.95, 0.80, 0.08)
    assert ok is True
    assert margin == pytest.approx(0.15, abs=1e-6)
    assert reason is None


def test_peak_uniqueness_gate_rejects_when_margin_below_required() -> None:
    ok, margin, reason = _apply_peak_uniqueness_gate(0.79, 0.75, 0.08)
    assert ok is False
    assert margin == pytest.approx(0.04, abs=1e-6)
    assert reason == "low_peak_uniqueness"


def test_peak_uniqueness_gate_disabled_when_min_margin_zero() -> None:
    """``peak_unique_margin: 0`` opts out (e.g. legitimately repeated UI element)."""
    ok, margin, reason = _apply_peak_uniqueness_gate(0.79, 0.78, 0.0)
    assert ok is True
    assert margin is None
    assert reason is None


def test_peak_uniqueness_gate_skipped_when_no_second_peak() -> None:
    """Heatmap too small for a structurally different 2nd peak → accept (1:1-ish)."""
    ok, margin, reason = _apply_peak_uniqueness_gate(0.95, None, 0.08)
    assert ok is True
    assert margin is None
    assert reason is None


def test_default_peak_unique_margin_is_used_when_yaml_field_absent() -> None:
    assert optional_peak_unique_margin({}) == DEFAULT_PEAK_UNIQUE_MARGIN
    assert optional_peak_unique_margin({"peak_unique_margin": None}) == DEFAULT_PEAK_UNIQUE_MARGIN
    assert optional_peak_unique_margin({"peak_unique_margin": "garbage"}) == DEFAULT_PEAK_UNIQUE_MARGIN
    assert optional_peak_unique_margin({"peak_unique_margin": 0}) == 0.0
    assert optional_peak_unique_margin({"peak_unique_margin": 0.15}) == pytest.approx(0.15)
    assert optional_peak_unique_margin({"peak_unique_margin": -0.5}) == 0.0
    assert optional_peak_unique_margin({"peak_unique_margin": 5}) == 1.0


# --------------------------------------------------------------------------------------
# Sliding template match exposes the 2nd-best NCC peak
# --------------------------------------------------------------------------------------


def test_sliding_match_reports_second_best_ncc_for_duplicated_template() -> None:
    """When the same content appears twice, the 2nd-best peak is near 1.0 → tight margin.

    This emulates the failure mode of low-info templates: another spot looks just as
    template-like as the actual landmark, so the 2nd-best peak is high and the gate fires.
    """
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 256, size=(300, 300, 3), dtype=np.uint8)
    template = frame[40:80, 40:80].copy()
    # Paste an identical copy elsewhere; both spots score ≈1.0 → tiny margin.
    frame[200:240, 200:240] = template
    search = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}
    res = match_template_in_search_roi_bbox_percent(frame, template, search)
    assert res["score_ncc_second"] is not None
    assert float(res["score_ncc_second"]) >= 0.95
    assert float(res["score_ncc"]) - float(res["score_ncc_second"]) < 0.05


def test_sliding_match_reports_large_margin_for_distinctive_template() -> None:
    """Template with strong unique features → big drop to 2nd peak."""
    frame = np.full((300, 300, 3), 120, dtype=np.uint8)
    # One dark cross at (150, 150), nothing else distinctive.
    cv2.line(frame, (140, 150), (160, 150), (10, 10, 10), 3)
    cv2.line(frame, (150, 140), (150, 160), (10, 10, 10), 3)
    template = frame[140:160, 140:160].copy()
    search = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}
    res = match_template_in_search_roi_bbox_percent(frame, template, search)
    margin = float(res["score_ncc"]) - float(res["score_ncc_second"] or 0.0)
    assert margin >= 0.20


# --------------------------------------------------------------------------------------
# Integration: hand_pointer_small must NOT match on a clean main_city screen
# --------------------------------------------------------------------------------------


def _overlay_out(img: np.ndarray) -> dict:
    doc = json.loads(_AREA.read_text(encoding="utf-8"))
    cfg = load_analyze_yaml(_ANALYZE)
    overlay = cfg.get("overlay")
    assert isinstance(overlay, list)
    return evaluate_overlay_rules(img, doc, REPO, overlay)


@pytest.mark.skipif(
    not (_DEPS and _FIXTURE_MAIN_CITY.is_file()),
    reason="hand_pointer assets or main_city fixture missing",
)
def test_hand_pointer_small_does_not_match_on_main_city() -> None:
    """Regression: low-info smooth template used to false-match on main_city.

    Without the gate, NCC≈0.79 on main_city beats threshold 0.75 → enqueues bogus tutorial
    scenario. With the gate, peak-margin (~0.04) < 0.08 default → matched=false with
    reason=low_peak_uniqueness.
    """
    img = cv2.imread(str(_FIXTURE_MAIN_CITY))
    assert img is not None
    out = _overlay_out(img)
    row = out.get("hand_pointer_small.visible")
    assert isinstance(row, dict), out
    assert row.get("matched") is False, row
    # When NCC alone clears the threshold, the new gate is what failed.
    if float(row.get("score") or 0) >= float(row.get("threshold") or 1):
        assert row.get("reason") == "low_peak_uniqueness", row


@pytest.mark.skipif(
    not (_DEPS and _FIXTURE_HP_SCREEN.is_file()),
    reason="hand_pointer fixture missing",
)
def test_hand_pointer_small_still_matches_on_real_tutorial_frame() -> None:
    """Real tutorial frame still passes (margin ≈0.23 on this fixture)."""
    img = cv2.imread(str(_FIXTURE_HP_SCREEN))
    assert img is not None
    out = _overlay_out(img)
    row = out.get("hand_pointer_small.visible")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    # Sanity: gate observed margin should be well above the default minimum.
    margin = row.get("peak_unique_margin_observed")
    assert margin is not None
    assert float(margin) >= DEFAULT_PEAK_UNIQUE_MARGIN
