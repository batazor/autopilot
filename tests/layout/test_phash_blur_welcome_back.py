"""Regression: blur-before-pHash lifts welcome_back live-frame score without hurting shop."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.template_match import _phash_match_score, patch_bgr_from_bbox_percent

REPO = Path(__file__).resolve().parents[2]
FRAME = REPO / "temporal/bs1_current_state.png"
WELCOME_BACK_BBOX = {
    "x": 35.13899613899614,
    "y": 18.515217391304347,
    "width": 29.44401544401545,
    "height": 2.1869565217391305,
}
TEMPLATE = (
    REPO
    / "modules/core/welcome_back/references/crop/welcome_back_text.welcome_back.png"
)
THRESHOLD = 0.9


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason=(
        "Anchored to ``references/temporal/bs1_current_state.png`` which is a "
        "rolling ADB preview the worker overwrites on every snapshot — its "
        "contents are not under repo control. Passes when the last frame "
        "captured by the bot happened to show the welcome_back popup, fails "
        "otherwise. Re-point at a frozen fixture under references/ to make "
        "the assertion deterministic."
    ),
)
async def test_welcome_back_live_frame_passes_threshold() -> None:
    frame = cv2.imread(str(FRAME))
    tpl = cv2.imread(str(TEMPLATE))
    assert frame is not None and tpl is not None

    patch, _ = patch_bgr_from_bbox_percent(frame, WELCOME_BACK_BBOX)
    score, hamming = _phash_match_score(patch, tpl)
    assert score >= THRESHOLD, f"pHash score {score} hamming={hamming}"

    area_doc = load_area_doc(REPO)
    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO,
        [
            {
                "name": "t",
                "region": "text.welcome_back",
                "action": "findIcon",
                "threshold": THRESHOLD,
            }
        ],
    )
    row = out["t"]
    assert row.get("matched") is True, row
    assert float(row.get("score") or 0) >= THRESHOLD
