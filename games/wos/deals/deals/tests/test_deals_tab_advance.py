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
    # Namespace membership is by screen id, not module directory: vault_of_enigma
    # lives under games/wos/events/ yet its screen id is ``deals.vault_of_enigma``
    # so its tab template is still discovered for the deals strip.
    assert "deals.vault_of_enigma" in templates


@pytest.mark.asyncio
async def test_deals_detect_tabs_falls_back_when_red_dot_pages_are_unidentified(
    area_doc: dict,
) -> None:
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
    assert hit["red_dot_pages"] == []
    assert hit["pushScenario"] == [
        {
            "type": "tabs.strip.advance",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 15,
            "args": {"region": "deals.tabs_strip"},
        },
    ]


@pytest.mark.asyncio
async def test_deals_detect_tabs_can_push_identified_red_dot_pages_when_opted_in(
    area_doc: dict,
) -> None:
    frame = _load_bgr("games/wos/deals/hero_rally/references/hero_rally.main.png")
    rules = [
        {
            "name": "deals.tabs.visible_red_dot",
            "region": "deals.tabs_strip",
            "action": "detectTabs",
            "namespace": "deals",
            "template_min_score": 0.45,
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
    assert hit["red_dot_pages"] == ["deals.hall_of_heroes", "deals.sign_in"]
    assert hit["pushScenario"] == [
        {"type": "deals.hall_of_heroes", "dsl_scenario": None, "priority": None, "ttl": 15},
        {"type": "deals.sign_in", "dsl_scenario": None, "priority": None, "ttl": 15},
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
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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


# ── Carousel left-arrow fallback (deals.next.left) ──────────────────────────

ANALYZE_PATH = MODULE_DIR / "analyze" / "analyze.yaml"
NEXT_LEFT_REGION = "deals.next.left"
DEALS_SCREENS = {
    "deals",
    "deals.sign_in",
    "deals.home_and_beyond",
    "deals.hall_of_heroes",
    "deals.vault_of_enigma",
    "deals.hero_rally",
    "deals.bank",
    "deals.dead_shot",
    "deals.endless_wayfarer",
    "deals.journey_treasures",
    "deals.tundra_trading_station",
}


def test_analyze_arrow_rule_pushes_with_throttle() -> None:
    """The ``<`` arrow rule must mirror the shop carousel contract: one rule,
    every deals screen listed (no wildcard support in the analyzer), push args
    carrying both the strip region and the arrow as ``next_region``, and a
    unit-suffixed TTL so the 1 Hz overlay loop can't hot-loop the queue."""
    from analysis.overlay_manifest import load_analyze_yaml

    doc = load_analyze_yaml(ANALYZE_PATH)
    rules = [r for r in doc.get("overlay", []) if r.get("region") == NEXT_LEFT_REGION]
    assert len(rules) == 1, f"expected exactly one arrow rule, got {rules!r}"
    (rule,) = rules
    assert set(rule["screens"]) == DEALS_SCREENS, (
        f"screens drift: {set(rule['screens']) ^ DEALS_SCREENS}"
    )
    pushes = [
        step["push_scenario"]
        for step in rule.get("steps") or []
        if isinstance(step, dict) and isinstance(step.get("push_scenario"), dict)
    ]
    assert len(pushes) == 1 and pushes[0]["name"] == "tabs.strip.advance"
    assert pushes[0]["args"] == {
        "region": "deals.tabs_strip",
        "next_region": NEXT_LEFT_REGION,
    }
    ttl = str(pushes[0].get("ttl", "")).strip()
    assert ttl.endswith(("s", "m", "h")), f"unexpected TTL form: {ttl!r}"


def test_analyze_red_dot_rule_passes_next_region() -> None:
    """The detectTabs fallback push must also carry the arrow so the helper
    can page the strip when red dots are visible but tabs are unidentified."""
    from analysis.overlay_manifest import load_analyze_yaml

    doc = load_analyze_yaml(ANALYZE_PATH)
    rules = [
        r for r in doc.get("overlay", []) if r.get("name") == "deals.tabs.visible_red_dot"
    ]
    assert len(rules) == 1
    (push_step,) = rules[0]["steps"]
    assert push_step["push_scenario"]["args"] == {
        "region": "deals.tabs_strip",
        "next_region": NEXT_LEFT_REGION,
    }


@pytest.mark.asyncio
async def test_arrow_detected_only_when_rendered(area_doc: dict) -> None:
    """findIcon matches the arrow on the frame it was labeled on and stays
    silent on frames without it — this is what closes the cross-tick loop:
    no arrow → no match → no further ``tabs.strip.advance`` pushes."""
    rule = {
        "name": "deals.tabs.advance.has_prev_page",
        "region": NEXT_LEFT_REGION,
        "action": "findIcon",
        "threshold": 0.8,
        "screens": ["deals"],
    }
    for rel, expected in [
        ("games/wos/deals/dead_shot/references/main.png", True),
        ("games/wos/deals/deals/references/deals.png", False),
    ]:
        out = await evaluate_overlay_rules_async(
            _load_bgr(rel),
            area_doc,
            REPO_ROOT,
            [rule],
            current_screen="deals",
        )
        hit = out["deals.tabs.advance.has_prev_page"]
        assert hit["matched"] is expected, f"{rel}: {hit}"


@pytest.mark.asyncio
async def test_click_next_red_dot_tab_advances_via_left_arrow(mocker) -> None:
    """No red-dot tab left → the helper taps ``deals.next.left`` so the bot
    still reaches deals events that haven't been processed yet."""
    from layout.tabs_strip_navigator import StripAction

    frame = _load_bgr("games/wos/deals/dead_shot/references/main.png")
    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)
    mocker.patch(
        "tasks.dsl_exec.red_dots.pick_next_strip_action",
        return_value=StripAction(kind="advance_page"),
    )

    ctx = dsl_exec.DslExecContext(
        redis_client=None,
        player_id="",
        instance_id="bs1",
        args={"region": "deals.tabs_strip", "next_region": NEXT_LEFT_REGION},
    )
    await dsl_exec.DSL_EXEC_REGISTRY["click_next_red_dot_tab"](ctx)

    assert ctx.result["action"] == "advanced_page"
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=NEXT_LEFT_REGION)
    ]
