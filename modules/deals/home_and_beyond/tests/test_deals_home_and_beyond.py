"""Detect the Home & Beyond page landmark + the Claim-All CTA.

The Home & Beyond page renders a multi-day claim grid with a single big
"Claim All" CTA at the bottom. The ``deals.home_and_beyond`` scenario just
tap-loops that CTA via ``while_match`` until it disappears.

Region names carry the in-game spelling ``beyound`` (typo) as labeled by the
annotator — only the FSM ``screen_id`` keeps the correct ``beyond``.

Contracts covered:

* ``deals.home_and_beyound.title`` is detected via ``findIcon`` on the
  reference — used by ``screen_verify`` to identify the page and by the
  analyze rule to gate the claim push.
* ``deals.home_and_beyound.claim_all`` is detected via ``findIcon`` so the
  DSL ``while_match`` step advances at least once before exiting.
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
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
TITLE_REGION = "deals.home_and_beyound.title"
CLAIM_REGION = "deals.home_and_beyound.claim_all"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rule_name", "region_name"),
    [
        ("deals.home_and_beyond.page", TITLE_REGION),
        ("deals.home_and_beyond.claim_all_visible", CLAIM_REGION),
    ],
)
async def test_home_and_beyond_regions_detected(
    area_doc: dict, rule_name: str, region_name: str,
) -> None:
    """Title + Claim-All regions both match on the labeled reference."""
    frame = _load_bgr("deals.home_and_beyound.png")

    rule = {
        "name": rule_name,
        "region": region_name,
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule],
        current_screen="deals.home_and_beyond",
    )
    hit = out[rule_name]
    assert hit["matched"] is True, (
        f"[{region_name}] not detected on deals.home_and_beyound.png – row: {hit}"
    )
