"""Detect the Witness sub-page landmark.

``hall_of_heroes.witness`` is reached by tapping ``deals.hall_of_heroes.add``
on the Hall of Heroes hub (see ``modules/deals/routes/edge_taps.yaml``). The
back chevron returns to the hub. Tests pin:

* The landmark used by ``screen_verify`` to identify the witness page.
* The number of ``button.free`` matches the DSL ``while_match`` step will
  iterate on — currently two (top "Event ends in" claim + Use Gems section).
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
TITLE_REGION = "hall_of_heroes.witness.title"
FREE_BUTTON_REGION = "button.free"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_witness_title_landmark_detected(area_doc: dict) -> None:
    """``hall_of_heroes.witness.title`` matches its template crop on the reference."""
    frame = _load_bgr("hall_of_heroes.witness.png")

    rule = {
        "name": "hall_of_heroes.witness.page",
        "region": TITLE_REGION,
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule],
        current_screen="hall_of_heroes.witness",
    )
    hit = out["hall_of_heroes.witness.page"]
    assert hit["matched"] is True, (
        f"[{TITLE_REGION}] landmark not detected on hall_of_heroes.witness.png – row: {hit}"
    )


@pytest.mark.asyncio
async def test_witness_button_free_count_is_two(area_doc: dict) -> None:
    """Whole-frame ``button.free`` search returns exactly two clickable buttons.

    The DSL ``while_match: button.free`` loop in
    ``modules/deals/scenarios/hall_of_heroes.witness.yaml`` claims each Free
    button in turn (one tap → wait → re-detect). On the captured reference
    the witness page exposes two buttons: the top "Event ends in" claim and
    the Use Gems section. Both must be detected at the production threshold
    (``isSearch: true`` on the region triggers full-frame search).
    """
    frame = _load_bgr("hall_of_heroes.witness.png")

    found: list[dict] = []
    excl: list[tuple[int, int]] = []
    threshold = 0.9
    for _ in range(8):
        rule = {
            "name": "witness.free",
            "region": FREE_BUTTON_REGION,
            "action": "exist",
            "threshold": threshold,
            "exclude_top_lefts": list(excl),
            "exclude_radius_px": 24,
        }
        out = await evaluate_overlay_rules_async(
            frame, area_doc, REPO_ROOT, [rule],
            current_screen="hall_of_heroes.witness",
        )
        row = out["witness.free"]
        if not row.get("matched"):
            break
        tl = row.get("top_left") or (0, 0)
        found.append(
            {
                "top_left": (int(tl[0]), int(tl[1])),
                "score": float(row.get("score") or 0.0),
            }
        )
        excl.append((int(tl[0]), int(tl[1])))

    assert len(found) == 2, (
        f"expected exactly 2 button.free matches on witness ref, "
        f"got {len(found)}: {found}"
    )
