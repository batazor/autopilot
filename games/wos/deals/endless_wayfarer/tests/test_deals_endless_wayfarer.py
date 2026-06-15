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


def test_endless_wayfarer_scenario_claims_white_border_only() -> None:
    scenario = yaml.safe_load((MODULE_DIR / "scenarios/deals.endless_wayfarer.yaml").read_text())

    assert scenario["node"] == "deals.endless_wayfarer"
    step = scenario["steps"][0]
    assert step["while_match"] == "button.claim"
    assert step["isWhiteBorder"] is True
    assert {"click": "button.claim"} in step["steps"]


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
