"""Unit coverage for the ``isWhiteBorder:`` filter on DSL ``match:`` steps.

Two layers, mirroring ``test_dsl_is_red_dot.py``:

* parser (``_step_white_border_requirement``) — YAML bool only.
* row builder (``DslScenarioTask._build_white_border_only_row``) — present /
  absent / unexpected-present, plus the missing-bbox guard.

No Redis, no asyncio. Heavy integration is covered by the live-detector
fixture tests in ``test_white_border_detector.py``.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

import tasks.dsl_scenario as dsl
from tasks.dsl_scenario_helpers import _step_white_border_requirement

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DYN_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "white_border_dyn_1.png"


def test_step_white_border_requirement_reads_bool_only() -> None:
    assert _step_white_border_requirement({"isWhiteBorder": True}) is True
    assert _step_white_border_requirement({"isWhiteBorder": False}) is False
    assert _step_white_border_requirement({}) is None
    assert _step_white_border_requirement({"isWhiteBorder": "yes"}) is None


def _frame_with_white_halo(
    w: int = 200, h: int = 200, *, with_halo: bool
) -> np.ndarray:
    """200×200 frame holding a purple-ish icon body in the center.

    ``with_halo=True``: card background is near-white → halo around the icon
    bbox is bright + desaturated → detector fires.
    ``with_halo=False``: card background is bright cyan → halo is bright but
    saturated → detector stays quiet (same shape, different colour gates)."""
    bg = (245, 245, 245) if with_halo else (220, 200, 50)  # BGR → bright cyan when no halo
    img = np.full((h, w, 3), bg, dtype=np.uint8)
    cv2.rectangle(img, (70, 70), (130, 130), (160, 50, 130), thickness=-1)
    return img


def test_build_white_border_only_row_matches_when_halo_present() -> None:
    region_def = {
        "name": "reward.tile",
        "bbox": {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0},
    }
    img = _frame_with_white_halo(with_halo=True)
    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="reward.tile",
        region_def=region_def,
        image_bgr=img,
        requirement=True,
    )
    assert out["matched"] is True
    assert out["white_border_present"] is True
    assert out["white_border_required"] is True
    assert out["action"] == "white_border"
    # Tap coords default to bbox center for the follow-up click step.
    assert out["tap_x_pct"] == 35.0 + 30.0 / 2.0
    assert out["tap_y_pct"] == 35.0 + 30.0 / 2.0


def test_build_white_border_only_row_misses_when_halo_absent() -> None:
    region_def = {
        "name": "reward.tile",
        "bbox": {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0},
    }
    img = _frame_with_white_halo(with_halo=False)
    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="reward.tile",
        region_def=region_def,
        image_bgr=img,
        requirement=True,
    )
    assert out["matched"] is False
    assert out["white_border_present"] is False
    assert out["reason"] == "white_border_missing"


def test_build_white_border_only_row_misses_when_halo_unexpectedly_present() -> None:
    """``isWhiteBorder: false`` semantics — halo present but the step expects
    it absent → guard rejects."""
    region_def = {
        "name": "reward.tile",
        "bbox": {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0},
    }
    img = _frame_with_white_halo(with_halo=True)
    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="reward.tile",
        region_def=region_def,
        image_bgr=img,
        requirement=False,
    )
    assert out["matched"] is False
    assert out["white_border_present"] is True
    assert out["reason"] == "white_border_unexpected"


def test_build_white_border_only_row_errors_without_bbox() -> None:
    """No bbox in area.json → safe failure with a typed reason instead of
    raising. Mirrors the missing-bbox guard on the red-dot / tab-active paths."""
    region_def = {"name": "reward.tile"}
    img = _frame_with_white_halo(with_halo=True)
    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="reward.tile",
        region_def=region_def,
        image_bgr=img,
        requirement=True,
    )
    assert out["matched"] is False
    assert out["reason"] == "missing_bbox_for_white_border"


def test_white_border_falls_back_to_search_sibling_bbox() -> None:
    """When the primary bbox yields no slide-find candidate, the matcher must
    retry against the ``{region}_search`` sibling bbox passed by the caller.

    Regression guard for the ``claim_trials`` issue: ``button.claim``'s
    primary bbox is narrow and misses VIP-style popups where the highlighted
    claim button lives outside it. The findIcon path already auto-resolves
    ``button.claim_search``; the white_border guard must do the same.
    """
    img = cv2.imread(str(_DYN_FIXTURE))
    assert img is not None, f"missing fixture: {_DYN_FIXTURE}"

    # Bottom-right corner — no highlight, no halo (verified empirically).
    primary = {
        "name": "button.claim",
        "bbox": {"x": 85.0, "y": 92.0, "width": 10.0, "height": 5.0},
    }
    # Search sibling covers (almost) the whole popup — includes row 1 highlight.
    search_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    # Without the sibling: misses (this is the pre-fix behavior).
    no_sibling = dsl.DslScenarioTask._build_white_border_only_row(
        region="button.claim",
        region_def=primary,
        image_bgr=img,
        requirement=True,
    )
    assert no_sibling["matched"] is False

    # With the sibling: slide-find finds the highlighted row, matched=True,
    # and the row is tagged with the source so debug UIs can show provenance.
    with_sibling = dsl.DslScenarioTask._build_white_border_only_row(
        region="button.claim",
        region_def=primary,
        image_bgr=img,
        requirement=True,
        search_bbox=search_bbox,
    )
    assert with_sibling["matched"] is True
    assert with_sibling["white_border_present"] is True
    assert with_sibling.get("search_source") == "search_sibling"
    # Tap coords land on row 1 (around 22-35% x, 23-30% y in the 720×1280 frame).
    assert 15.0 <= with_sibling["tap_x_pct"] <= 45.0
    assert 18.0 <= with_sibling["tap_y_pct"] <= 35.0


def test_white_border_prefers_primary_over_search_sibling() -> None:
    """When the primary bbox already yields a slide-find candidate, the
    sibling fallback must not run — the primary match wins and the row is
    tagged ``source="primary"``.
    """
    img = cv2.imread(str(_DYN_FIXTURE))
    assert img is not None

    # Primary bbox covers row 1 directly (where the highlight is).
    primary = {
        "name": "button.claim",
        "bbox": {"x": 15.0, "y": 18.0, "width": 30.0, "height": 17.0},
    }
    # Unrelated wider search sibling (must not override primary).
    search_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="button.claim",
        region_def=primary,
        image_bgr=img,
        requirement=True,
        search_bbox=search_bbox,
    )
    assert out["matched"] is True
    assert out.get("search_source") == "primary"


def test_build_white_border_only_row_honors_step_overrides() -> None:
    """Per-step ``max_mean_saturation`` / ``min_mean_value`` overrides the
    module defaults, mirroring the tab_active path. Useful when a particular
    region needs a tighter or looser gate without touching detector defaults."""
    region_def = {
        "name": "reward.tile",
        "bbox": {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0},
    }
    img = _frame_with_white_halo(with_halo=True)  # halo S≈0 (pure white)
    # Crank the saturation gate down so even a perfect white halo fails.
    out = dsl.DslScenarioTask._build_white_border_only_row(
        region="reward.tile",
        region_def=region_def,
        image_bgr=img,
        requirement=True,
        step={"max_mean_saturation": -1.0},
    )
    assert out["matched"] is False
    assert out["white_border_present"] is False
