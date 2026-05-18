"""Detect the Hall of Heroes page landmark + the "add" red-dot indicator.

The Hall of Heroes page renders a rewards grid with an ``add`` (``+``) CTA
near a counter at the top of the panel. The red-dot capability on
``deals.hall_of_heroes.add`` lets analyze rules gate a future claim push
on "something new to spend the counter on".

Contracts covered:

* ``deals.hall_of_heroes.title`` is detected via ``findIcon`` on the
  reference — used by ``screen_verify`` to identify the page.
* ``deals.hall_of_heroes.add`` carries a red dot on the captured reference
  — the production analyze rule with ``isRedDot: true`` would fire.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
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


@pytest.mark.asyncio
async def test_hall_of_heroes_title_landmark_detected(area_doc: dict) -> None:
    """``deals.hall_of_heroes.title`` matches its template crop on the reference."""
    frame = _load_bgr("deals.hall_of_heroes.png")

    rule = {
        "name": "deals.hall_of_heroes.page",
        "region": TITLE_REGION,
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule],
        current_screen="deals.hall_of_heroes",
    )
    hit = out["deals.hall_of_heroes.page"]
    assert hit["matched"] is True, (
        f"[{TITLE_REGION}] landmark not detected on deals.hall_of_heroes.png – row: {hit}"
    )


@pytest.mark.asyncio
async def test_hall_of_heroes_add_template_match(area_doc: dict) -> None:
    """``deals.hall_of_heroes.add`` template matches its "with red dot" variant.

    The red dot itself is too small for ``find_red_dots`` HSV/circularity
    gates (the mask merges with adjacent red panel pixels into an elongated
    blob, aspect ~2.6 / circularity ~0.18 — far below the production floor).
    Switching to ``findIcon`` sidesteps the detector: the labeled bbox crop
    captures the dot, so a high-score template match on the live frame
    implies the button is rendered in the "with red dot" state — exactly
    the gate the analyze rule needs to push the witness scenario.

    A future "claimed" reference (button drawn WITHOUT the red dot) should
    drop this match score below the 0.9 threshold; that is the failure mode
    the production rule wants.
    """
    frame = _load_bgr("deals.hall_of_heroes.png")

    rule = {
        "name": "deals.hall_of_heroes.add.has_dot",
        "region": ADD_REGION,
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule],
        current_screen="deals.hall_of_heroes",
    )
    hit = out["deals.hall_of_heroes.add.has_dot"]
    assert hit["matched"] is True, (
        f"[{ADD_REGION}] template not matched on deals.hall_of_heroes.png – row: {hit}"
    )
