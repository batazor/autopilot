"""Detect the Hall of Heroes page landmark + the "add" red-dot indicator.

The Hall of Heroes page renders a rewards grid with an ``add`` (``+``) CTA
near a counter at the top of the panel. The red-dot capability on
``deals.hall_of_heroes.add`` lets analyze rules gate a future claim push
on "something new to spend the counter on".

Contracts covered:

* ``deals.hall_of_heroes.title`` is an OCR text region — used by
  ``screen_verify`` to identify the page by title text.
* ``deals.hall_of_heroes.add`` carries a red dot on the captured reference,
  and the production analyze rule is explicitly gated with ``isRedDot: true``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
TITLE_REGION = "deals.hall_of_heroes.title"
ADD_REGION = "deals.hall_of_heroes.add"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


def test_hall_of_heroes_title_landmark_uses_ocr_text() -> None:
    """``deals.hall_of_heroes.title`` is verified by OCR text, not image match."""
    area = yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))
    screen = next(
        s for s in area["screens"] if s.get("screen_id") == "deals.hall_of_heroes"
    )
    regions = {r["name"]: r for r in screen.get("regions") or []}

    assert regions[TITLE_REGION]["action"] == "text"
    assert regions[TITLE_REGION]["type"] == "string"

    screen_graph.load_screen_verify_config.cache_clear()
    try:
        assert screen_graph.screen_verify_rules("deals.hall_of_heroes") == [
            {
                "ocr": TITLE_REGION,
                "contains": "Hall of Heroes",
                "threshold": 0.9,
            }
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_hall_of_heroes_add_template_match(area_doc: dict) -> None:
    """``deals.hall_of_heroes.add`` matches only with the red-dot gate."""
    frame = _load_bgr("main.png")
    analyze = yaml.safe_load(
        (MODULE_DIR / "analyze" / "analyze.yaml").read_text(encoding="utf-8")
    )
    rule = analyze["overlay"][0]

    assert rule["region"] == ADD_REGION
    assert rule["isRedDot"] is True
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule],
        current_screen="deals.hall_of_heroes",
    )
    hit = out["deals.hall_of_heroes.add.has_dot"]
    assert hit["matched"] is True, (
        f"[{ADD_REGION}] template not matched on main.png – row: {hit}"
    )
    assert hit["red_dot_present"] is True
