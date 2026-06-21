"""Regression coverage for Endless Wayfarer claim routing."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
TITLE_REGION = "endless_wayfarer.title"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


def test_endless_wayfarer_scenario_claims_bordered_tile_in_grid() -> None:
    scenario = yaml.safe_load((MODULE_DIR / "scenarios/deals.endless_wayfarer.yaml").read_text())

    assert scenario["node"] == "deals.endless_wayfarer"
    step = scenario["steps"][0]
    # Claimables are gold-bordered tiles in the reward grid (the border's bright
    # core registers as a white border); button.claim doesn't match this screen.
    assert step["while_match"] == "endless_wayfarer.reward_grid"
    assert step["isWhiteBorder"] is True
    assert {"click": "endless_wayfarer.reward_grid"} in step["steps"]
    # Square, fixed-size constraint: reward tiles are ~100px squares. Without it
    # the border find also taps small non-square highlights once tiles are gone.
    assert step["min_side_px"] >= 80
    assert step["min_aspect"] >= 0.8
    assert step["max_aspect"] <= 1.25


def test_endless_wayfarer_white_border_finds_square_claimable_gem() -> None:
    """The reward grid yields a SQUARE, tile-sized white-border match on a gem."""
    from layout.white_border_detector import find_white_border_match_in_search_roi

    frame = cv2.imread(str(REPO_ROOT / "tests/fixtures/deals_endless_wayfarer_yellow_border.png"))
    assert frame is not None
    match = find_white_border_match_in_search_roi(
        frame,
        {"x": 3.0, "y": 42.0, "width": 95.0, "height": 56.0},
        min_side_px=80,
        max_side_px=150,
        min_aspect=0.8,
        max_aspect=1.25,
    )
    assert match is not None, "no square claimable tile found in reward grid"
    # Lands on a left-column gem tile, and the match is itself square + tile-sized.
    assert 15.0 <= float(match["cx_pct"]) <= 45.0
    assert 50.0 <= float(match["cy_pct"]) <= 90.0
    _x, _y, w, h = match["px_rect"]
    assert 80 <= w <= 150 and 80 <= h <= 150
    assert 0.8 <= w / h <= 1.25


@pytest.mark.asyncio
async def test_endless_wayfarer_page_pushes_claim_scenario(area_doc: dict) -> None:
    frame = _load_bgr("endless_wayfarer.png")
    analyze = load_analyze_yaml(MODULE_DIR / "analyze/analyze.yaml")
    rules = analyze["overlay"]

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        rules,
        current_screen="deals.endless_wayfarer",
    )

    hit = out["deals.endless_wayfarer.page"]
    assert hit["matched"] is True, f"[{TITLE_REGION}] not detected: {hit}"
    assert {"type": "deals.endless_wayfarer", "priority": None, "ttl": 60, "dsl_scenario": None} in hit[
        "pushScenario"
    ]
