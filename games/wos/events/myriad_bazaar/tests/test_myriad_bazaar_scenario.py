"""Myriad Bazaar: claim only the top unlocked ``Claim For Free`` button.

The reference screenshot shows two green ``Claim For Free`` buttons; the lower
one carries a lock badge. The crop is taken from the top button and the scenario
uses ``threshold: 0.95`` so the locked duplicate stays below the match bar.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"

CLAIM_REGION = "button.claim_for_free"
SCENARIO_THRESHOLD = 0.95
# First-row button center (~39.5% of 1280); locked duplicate is ~88.7%.
FIRST_BUTTON_MAX_TAP_Y_PCT = 55.0
LOCKED_BUTTON_MIN_TAP_Y_PCT = 75.0


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _claim_bbox() -> dict[str, float]:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == CLAIM_REGION:
                return region["bbox"]
    pytest.fail(f"region {CLAIM_REGION!r} not in merged area doc")


def _wipe_first_claim_button(frame: np.ndarray, bbox: dict[str, float]) -> None:
    """Remove the top ``Claim For Free`` so ``while_match`` cannot re-trigger."""
    h, w = frame.shape[:2]
    pad_x = int(float(bbox["width"]) / 100 * w * 0.35)
    pad_y = int(float(bbox["height"]) / 100 * h * 1.5)
    cx = int((float(bbox["x"]) + float(bbox["width"]) / 2) / 100 * w)
    cy = int((float(bbox["y"]) + float(bbox["height"]) / 2) / 100 * h)
    x0 = max(0, cx - pad_x)
    x1 = min(w, cx + pad_x)
    y0 = max(0, cy - pad_y)
    y1 = min(h, cy + pad_y)
    frame[y0:y1, x0:x1] = (80, 80, 80)


async def _enumerate_matches(
    frame: np.ndarray,
    *,
    threshold: float,
    max_hits: int = 10,
) -> list[dict[str, float | tuple[int, int]]]:
    area_doc = load_area_doc(REPO_ROOT)
    found: list[dict[str, float | tuple[int, int]]] = []
    excl: list[tuple[int, int]] = []
    for _ in range(max_hits):
        rule = {
            "name": "probe",
            "region": CLAIM_REGION,
            "action": "findIcon",
            "threshold": threshold,
            "exclude_top_lefts": list(excl),
            "exclude_radius_px": 24,
        }
        out = await evaluate_overlay_rules_async(
            frame,
            area_doc,
            REPO_ROOT,
            [rule],
            current_screen="myriad_bazaar",
        )
        row = out["probe"]
        if not row.get("matched"):
            break
        tl = row.get("top_left") or (0, 0)
        found.append(
            {
                "top_left": (int(tl[0]), int(tl[1])),
                "tap_y_pct": float(row.get("tap_y_pct") or 0.0),
                "score": float(row.get("score") or 0.0),
            }
        )
        excl.append((int(tl[0]), int(tl[1])))
    return found


@pytest.fixture
def claim_bbox() -> dict[str, float]:
    return _claim_bbox()


@pytest.mark.asyncio
async def test_claim_for_free_single_match_at_scenario_threshold() -> None:
    """Scenario threshold must admit only the unlocked top button."""
    frame = _load_bgr("myriad_bazaar.png")
    matches = await _enumerate_matches(frame, threshold=SCENARIO_THRESHOLD)
    assert len(matches) == 1
    assert matches[0]["tap_y_pct"] < FIRST_BUTTON_MAX_TAP_Y_PCT


@pytest.mark.asyncio
async def test_locked_claim_for_free_duplicate_below_scenario_threshold() -> None:
    """The lower green button is similar but must not pass ``threshold: 0.95``."""
    frame = _load_bgr("myriad_bazaar.png")
    matches = await _enumerate_matches(frame, threshold=0.9)
    assert len(matches) == 2
    assert matches[0]["tap_y_pct"] < FIRST_BUTTON_MAX_TAP_Y_PCT
    assert matches[1]["tap_y_pct"] > LOCKED_BUTTON_MIN_TAP_Y_PCT
    assert matches[1]["score"] < SCENARIO_THRESHOLD


@pytest.mark.asyncio
async def test_myriad_bazaar_scenario_taps_top_button_once(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    claim_bbox: dict[str, float],
) -> None:
    """One iteration on the reference: click top free offer, then stop."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "myriad_bazaar"},
    )

    visible = _load_bgr("myriad_bazaar.png")
    after_claim = visible.copy()
    _wipe_first_claim_button(after_claim, claim_bbox)

    actions = make_actions([visible, after_claim, after_claim])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)
    mocker.patch.object(dsl, "click_approval_enabled", return_value=False)

    task = dsl.DslScenarioTask(
        task_id="myriad-bazaar-claim",
        player_id="p1",
        scenario_key="myriad_bazaar",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=CLAIM_REGION),
    ]
    tap_point = actions.tap.call_args_list[0][0][1]
    tap_y_pct = 100.0 * float(tap_point.y) / 1280.0
    assert tap_y_pct < FIRST_BUTTON_MAX_TAP_Y_PCT
