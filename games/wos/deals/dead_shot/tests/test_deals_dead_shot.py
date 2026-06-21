"""Regression coverage for Dead Shot claim routing."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
TITLE_REGION = "dead_shot.title"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_dead_shot_page_pushes_claim_scenario(area_doc: dict) -> None:
    frame = _load_bgr("main.png")
    analyze = load_analyze_yaml(MODULE_DIR / "analyze/analyze.yaml")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        analyze["overlay"],
        current_screen="deals.dead_shot",
    )

    hit = out["deals.dead_shot.page"]
    assert hit["matched"] is True, f"[{TITLE_REGION}] not detected: {hit}"
    assert {"type": "deals.dead_shot", "priority": None, "ttl": 60, "dsl_scenario": None} in hit[
        "pushScenario"
    ]
