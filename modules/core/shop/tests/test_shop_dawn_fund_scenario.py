"""Behavioural tests for the ``shop.dawn_fund`` scenario.

Loop body is a single ``while_match: shop.to.dawn_fund.box`` gated by
``isYellowGlow: true``. Two paths matter:

* **Claim path** — the reference shows tile 0 (100-diamond Free) with a
  yellow rim. Scenario taps once, the cleared frame ends the loop.
* **No-op path** — no glow on entry, scenario exits without tapping.

The tap point isn't the box bbox centre — it's the first glowing tile's
own centre (~tile-sized synthetic bbox with random jitter inside). That's
the natural-click contract: even if a future shop layout shifts the
column, the tap always lands inside the tile that actually glows.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
BOX_REGION = "shop.to.dawn_fund.box"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


def _box_bbox() -> dict[str, float]:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == BOX_REGION:
                return region["bbox"]
    pytest.fail(f"{BOX_REGION!r} not in area doc")


def _wipe_box_glow(frame: np.ndarray, bbox: dict[str, float]) -> None:
    """Paint the entire box dark purple so no warm-tone pixel survives.

    Simulates "all claims collected" — the column shows only locked tiles
    (or empty space). ``find_yellow_glow_squares`` will return zero
    candidates inside this bbox.
    """
    h, w = frame.shape[:2]
    x0 = int(bbox["x"] / 100 * w)
    x1 = int((bbox["x"] + bbox["width"]) / 100 * w)
    y0 = int(bbox["y"] / 100 * h)
    y1 = int((bbox["y"] + bbox["height"]) / 100 * h)
    frame[y0:y1, x0:x1] = (60, 30, 60)  # dark purple, low warmth


def test_shop_dawn_fund_scenario_file_exists() -> None:
    assert (MODULE_DIR / "scenarios" / "shop.dawn_fund.yaml").exists()


@pytest.fixture
def box_bbox() -> dict[str, float]:
    return _box_bbox()


@pytest.mark.asyncio
async def test_claims_glowing_tile_then_exits(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    box_bbox: dict[str, float],
) -> None:
    """Yellow glow present → 1 tap on the claim tile, then cleared frame ends loop."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.dawn_fund"},
    )

    frame_with_glow = _load_bgr("page.shop.dawn_fund.png")
    frame_cleared = frame_with_glow.copy()
    _wipe_box_glow(frame_cleared, box_bbox)

    actions = make_actions([frame_with_glow, frame_cleared])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-dawn-fund-claim",
        player_id="p1",
        scenario_key="shop.dawn_fund",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=BOX_REGION),
    ]


@pytest.mark.asyncio
async def test_no_op_when_no_glow(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    box_bbox: dict[str, float],
) -> None:
    """No glow on entry → scenario exits without tapping."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.dawn_fund"},
    )

    frame = _load_bgr("page.shop.dawn_fund.png")
    _wipe_box_glow(frame, box_bbox)

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-dawn-fund-noop",
        player_id="p1",
        scenario_key="shop.dawn_fund",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == []


@pytest.mark.asyncio
async def test_tap_target_is_glow_tile_not_box_centre(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    box_bbox: dict[str, float],
) -> None:
    """Tap lands on the glowing tile (~y=59 %), not the box centre (~y=75 %)."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.dawn_fund"},
    )

    frame_with_glow = _load_bgr("page.shop.dawn_fund.png")
    frame_cleared = frame_with_glow.copy()
    _wipe_box_glow(frame_cleared, box_bbox)

    actions = make_actions([frame_with_glow, frame_cleared])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-dawn-fund-aim",
        player_id="p1",
        scenario_key="shop.dawn_fund",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    await task.execute("bs1")

    assert actions.tap.call_count == 1
    args, _kwargs = actions.tap.call_args
    point = args[1]
    img_h = frame_with_glow.shape[0]
    tap_y_pct = point.y / img_h * 100
    # Glow tile centre is at y≈58.5%; box centre is at y≈75%.
    # Anywhere in [55%, 65%] is acceptable (15 % inset jitter inside ~92 px tile).
    assert 55 <= tap_y_pct <= 65, (
        f"tap y={tap_y_pct:.1f}% — expected ~58.5% (glow tile centre), not "
        f"box-bbox centre (~75 %). Suggests yellow_glow short-circuit isn't "
        f"setting tap target to the tile."
    )
