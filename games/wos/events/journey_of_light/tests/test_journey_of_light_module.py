from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from layout.area_manifest import load_area_doc

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


@pytest.mark.asyncio
async def test_journey_of_light_red_dot_pushes_scenario() -> None:
    frame = cv2.imread(str(MODULE_DIR / "references" / "main.png"))
    assert frame is not None
    area_doc = load_area_doc(REPO_ROOT)
    doc = load_analyze_yaml(MODULE_DIR / "analyze" / "analyze.yaml")
    rule = doc["overlay"][0]

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="deals",
    )

    hit = out["journey_of_light.add.has_red_dot"]
    assert hit["matched"] is True
    assert hit["red_dot_present"] is True
    assert hit["pushScenario"] == [
        {
            "type": "journey_of_light",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 60,
        }
    ]
