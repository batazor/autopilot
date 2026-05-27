"""Tests: deals event icons on the ``main_city`` reference screenshot.

Covered assertions
------------------
* Every deals entry icon (Events-style "Deals" gift box, "Sign-in" calendar)
  is detected via the ``template_icon`` pattern
  (``region: main_city.icon_search`` + per-icon template PNG) — same shape
  as ``modules/events/trials``.
* Dynamic red-dot status matches the captured snapshot: Deals and Sign-in
  carry a real red badge (so their production overlay rules with
  ``isRedDot: true`` push the ``deals.sign_in`` claim scenario);
Templates live under ``modules/deals/references/crop/`` and are auto-exported
by the annotator from the labeled ``main_city.to.<X>`` bboxes, so they stay
in sync when the bbox is moved — no manual re-cropping required.
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
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"

# (rule_name, template_rel, expected_red_dot_in_current_snapshot)
_ICONS: tuple[tuple[str, str, bool], ...] = (
    (
        "deals.main_city.event_icon.visible",
        "games/wos/deals/deals/references/crop/main_city_main_city.to.deals.png",
        True,
    ),
    (
        "deals.sign_in.main_city.event_icon.visible",
        "games/wos/deals/sign_in/references/crop/main_city_main_city.to.sign_in.png",
        True,
    ),
)


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rule_name", "template_rel"), [(r, t) for (r, t, _) in _ICONS],
)
async def test_main_city_deals_icon_template_match(
    area_doc: dict, rule_name: str, template_rel: str,
) -> None:
    """Every deals entry icon must be locatable in ``main_city.icon_search``."""
    frame = _load_bgr("main_city.png")

    rule = {
        "name": rule_name,
        "region": "main_city.icon_search",
        "action": "findIcon",
        "template": template_rel,
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule], current_screen="main_city",
    )
    hit = out[rule_name]

    assert hit["matched"] is True, (
        f"[{rule_name}] icon not detected on main_city.png – row: {hit}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rule_name", "template_rel", "expected_red_dot"), _ICONS,
)
async def test_main_city_deals_icon_red_dot_status(
    area_doc: dict, rule_name: str, template_rel: str, expected_red_dot: bool,
) -> None:
    """``isRedDot: true`` overlay rule fires iff a red badge is also present."""
    frame = _load_bgr("main_city.png")

    rule = {
        "name": rule_name,
        "region": "main_city.icon_search",
        "action": "findIcon",
        "template": template_rel,
        "threshold": 0.9,
        "isRedDot": True,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule], current_screen="main_city",
    )
    hit = out[rule_name]

    # ``matched`` is gated by both template match AND red-dot when isRedDot=True.
    assert hit["matched"] is expected_red_dot, (
        f"[{rule_name}] matched={hit['matched']} but expected {expected_red_dot} "
        f"(red_dot_present={hit.get('red_dot_present')}) – row: {hit}"
    )
    assert bool(hit.get("red_dot_present")) is expected_red_dot, (
        f"[{rule_name}] red_dot_present={hit.get('red_dot_present')} but "
        f"expected {expected_red_dot} – row: {hit}"
    )
