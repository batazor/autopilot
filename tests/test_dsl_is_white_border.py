"""Unit coverage for the ``isWhiteBorder:`` filter on DSL ``match:`` steps.

Two layers, mirroring ``test_dsl_is_red_dot.py``:

* parser (``_step_white_border_requirement``) — bool, snake_case, string aliases.
* row builder (``DslScenarioTask._build_white_border_only_row``) — present /
  absent / unexpected-present, plus the missing-bbox guard.

No Redis, no asyncio. Heavy integration is covered by the live-detector
fixture tests in ``test_white_border_detector.py``.
"""

from __future__ import annotations

import cv2
import numpy as np

import tasks.dsl_scenario as dsl
from tasks.dsl_scenario_helpers import _step_white_border_requirement


def test_step_white_border_requirement_reads_bool_and_aliases() -> None:
    assert _step_white_border_requirement({"isWhiteBorder": True}) is True
    assert _step_white_border_requirement({"isWhiteBorder": False}) is False
    assert _step_white_border_requirement({"is_white_border": True}) is True
    assert _step_white_border_requirement({"isWhiteBorder": "yes"}) is True
    assert _step_white_border_requirement({"isWhiteBorder": "off"}) is False
    assert _step_white_border_requirement({}) is None
    assert _step_white_border_requirement({"isWhiteBorder": "maybe"}) is None


def _frame_with_white_halo(
    w: int = 200, h: int = 200, *, with_halo: bool
) -> np.ndarray:
    """200×200 frame holding a purple-ish icon body in the center.

    ``with_halo=True``: card background is near-white → halo around the icon
    bbox is bright + desaturated → detector fires.
    ``with_halo=False``: card background is bright cyan → halo is bright but
    saturated → detector stays quiet (same shape, different colour gates)."""
    if with_halo:
        bg = (245, 245, 245)
    else:
        bg = (220, 200, 50)  # BGR → bright cyan
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
