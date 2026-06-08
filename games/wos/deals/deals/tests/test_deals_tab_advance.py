"""Deals tab-strip red-dot routing.

The analyzer should not leave the worker idle when a Deals page has visible
red-dot tabs. It queues ``deals.tab.advance`` at low priority; that scenario
clicks the first non-active dotted tab, then the destination page analyzer can
queue the concrete claim scenario.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from tasks import dsl_exec

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


def _load_bgr(rel: str) -> np.ndarray:
    path = REPO_ROOT / rel
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_deals_tabs_analyzer_pushes_tab_advance(area_doc: dict) -> None:
    frame = _load_bgr("games/wos/deals/home_and_beyond/references/deals.home_and_beyound.png")
    rules = [
        {
            "name": "deals.tabs.visible_red_dot",
            "region": "deals.tabs_strip",
            "action": "detectTabs",
            "screens": ["deals.home_and_beyond"],
            "steps": [{"push_scenario": {"name": "deals.tab.advance", "ttl": "15s"}}],
        }
    ]

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        rules,
        current_screen="deals.home_and_beyond",
    )

    hit = out["deals.tabs.visible_red_dot"]
    assert hit["matched"] is True
    assert hit["red_dot_indices"]
    assert hit["pushScenario"] == [
        {
            "type": "deals.tab.advance",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 15,
        }
    ]


@pytest.mark.asyncio
async def test_click_next_red_dot_tab_skips_active_tab(mocker) -> None:
    frame = _load_bgr("games/wos/deals/home_and_beyond/references/deals.home_and_beyound.png")
    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    ctx = dsl_exec.DslExecContext(
        redis_client=None,
        player_id="",
        instance_id="bs1",
        args={"region": "deals.tabs_strip"},
    )
    await dsl_exec.DSL_EXEC_REGISTRY["click_next_red_dot_tab"](ctx)

    assert ctx.result["action"] == "clicked_tab"
    assert ctx.result["tab_index"] == 0
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region="deals.tabs_strip")
    ]
