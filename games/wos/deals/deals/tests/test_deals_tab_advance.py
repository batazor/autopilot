"""Deals tab-strip red-dot routing.

The analyzer should not leave the worker idle when a Deals page has visible
red-dot tabs. It queues the shared ``tabs.strip.advance`` helper with the Deals
strip region; that scenario clicks the first non-active dotted tab, then the
destination page analyzer can queue the concrete claim scenario.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.tabs_strip_identifier import discover_tab_templates
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
            "steps": [
                {
                    "push_scenario": {
                        "name": "tabs.strip.advance",
                        "ttl": "15s",
                        "args": {"region": "deals.tabs_strip"},
                    }
                }
            ],
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
    assert hit["active_page_id"] == "deals.home_and_beyond"
    assert hit["pushScenario"] == [
        {
            "type": "tabs.strip.advance",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 15,
            "args": {"region": "deals.tabs_strip"},
        }
    ]


def test_deals_tab_templates_are_discovered(area_doc: dict) -> None:
    pair = screen_region_by_name(area_doc, "deals.tabs_strip")
    assert pair is not None
    templates = discover_tab_templates(
        area_doc,
        REPO_ROOT,
        pair[1]["bbox"],
        namespace="deals",
    )

    assert "deals.hero_rally" in templates
    assert "deals.home_and_beyond" in templates


@pytest.mark.asyncio
async def test_deals_detect_tabs_pushes_identified_red_dot_pages(area_doc: dict) -> None:
    frame = _load_bgr("games/wos/deals/hero_rally/references/hero_rally.main.png")
    rules = [
        {
            "name": "deals.tabs.visible_red_dot",
            "region": "deals.tabs_strip",
            "action": "detectTabs",
            "namespace": "deals",
            "push_red_dot_pages": True,
            "screens": ["deals.hero_rally"],
            "steps": [
                {
                    "push_scenario": {
                        "name": "tabs.strip.advance",
                        "ttl": "15s",
                        "args": {"region": "deals.tabs_strip"},
                    }
                }
            ],
        }
    ]

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        rules,
        current_screen="deals.hero_rally",
    )

    hit = out["deals.tabs.visible_red_dot"]
    assert hit["active_page_id"] == "deals.hero_rally"
    assert hit["red_dot_pages"] == ["deals.hall_of_heroes", "deals.sign_in"]
    assert hit["pushScenario"] == [
        {
            "type": "deals.hall_of_heroes",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 15,
        },
        {
            "type": "deals.sign_in",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 15,
        },
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


@pytest.mark.asyncio
async def test_click_next_red_dot_tab_skips_foreign_screen(
    mocker,
    redis_async: object,
) -> None:
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"current_screen": "shop"},
    )
    frame = _load_bgr("games/wos/deals/home_and_beyond/references/deals.home_and_beyound.png")
    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={"region": "deals.tabs_strip"},
    )
    await dsl_exec.DSL_EXEC_REGISTRY["click_next_red_dot_tab"](ctx)

    assert ctx.result["action"] == "screen_mismatch"
    assert actions.tap.call_args_list == []
