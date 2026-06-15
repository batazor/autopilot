from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from layout.area_manifest import load_area_doc
from navigation import screen_graph, tab_index_resolver  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
SCREEN = "journey_of_light.journey_of_light"
TREASURES_SCREEN = "journey_of_light.my_treasures"


def _load_bgr(name: str):
    frame = cv2.imread(str(MODULE_DIR / "references" / name))
    assert frame is not None, f"missing reference: {name}"
    return frame


def _analyze_rule(name: str) -> dict:
    doc = load_analyze_yaml(MODULE_DIR / "analyze" / "analyze.yaml")
    return next(rule for rule in doc["overlay"] if rule["name"] == name)


def _load_scenario(name: str) -> dict:
    path = MODULE_DIR / "scenarios" / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _claim_all_frame():
    frame = _load_bgr("main.png")
    crop = cv2.imread(
        str(REPO_ROOT / "games/wos/core/common/references/crop/claim_all_claim_all.png")
    )
    assert crop is not None
    # Same physical CTA as Dispatch All, with the green Claim All text state pasted in.
    frame[1165:1215, 70:280] = frame[1160, 80]
    frame[1170 : 1170 + crop.shape[0], 95 : 95 + crop.shape[1]] = crop
    return frame


def _dim_dispatch_all_frame():
    frame = _load_bgr("main.png")
    crop = cv2.imread(
        str(MODULE_DIR / "references/crop/main_journey_of_light.dispatch_all.png")
    )
    assert crop is not None
    # Keep the button structure/text, but make the CTA visually disabled.
    frame[1156:1222, 60:284] = (crop * 0.05).astype(crop.dtype)
    return frame


def _disabled_my_treasures_enable_frame():
    frame = _load_bgr("my_treasures.png")
    # Paste the lower grey Enable buttons over the two active green slots.
    frame[724:785, 60:198] = frame[1158:1219, 130:268]
    frame[724:785, 384:522] = frame[1158:1219, 454:592]
    return frame


def _load_popup_bgr():
    frame = _load_bgr("my_treasures_enable_popup.png")
    assert frame is not None
    return frame


def _load_assemble_popup_bgr():
    frame = _load_bgr("my_treasures_assemble_popup.png")
    assert frame is not None
    return frame


@pytest.mark.asyncio
async def test_dispatch_all_analyzer_pushes_dispatch_scenario() -> None:
    frame = _load_bgr("main.png")
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.dispatch_all.visible")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen=SCREEN,
    )

    hit = out["journey_of_light.dispatch_all.visible"]
    assert hit["matched"] is True
    assert hit["pushScenario"] == [
        {
            "type": "journey_of_light.dispatch_all",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 60,
        }
    ]


@pytest.mark.asyncio
async def test_dispatch_all_analyzer_rejects_disabled_dim_button() -> None:
    frame = _dim_dispatch_all_frame()
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.dispatch_all.visible")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen=SCREEN,
    )

    hit = out["journey_of_light.dispatch_all.visible"]
    assert hit["matched"] is False
    if "patch_bright_ratio" in hit:
        assert hit["patch_bright_ratio"] < hit["min_patch_bright_ratio"]
    assert "pushScenario" in hit


@pytest.mark.asyncio
async def test_claim_all_analyzer_pushes_claim_scenario() -> None:
    frame = _claim_all_frame()
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.claim_all.visible")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen=SCREEN,
    )

    hit = out["journey_of_light.claim_all.visible"]
    assert hit["matched"] is True
    assert hit["pushScenario"] == [
        {
            "type": "journey_of_light.claim_all",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 60,
        }
    ]


def test_claim_all_scenario_does_not_click_generic_exit_region() -> None:
    scenario = _load_scenario("journey_of_light.claim_all.yaml")

    assert "tapanywhereyoexit" not in str(scenario["steps"])
    assert scenario["node"] == SCREEN
    assert scenario["steps"][0]["template"] == (
        "games/wos/core/common/references/crop/claim_all_claim_all.png"
    )
    assert scenario["steps"][0]["threshold"] == 0.75
    assert scenario["steps"][0]["min_match_saturation"] == 80
    assert scenario["steps"][0]["min_patch_bright_ratio"] == 0.05


def test_dispatch_all_scenario_checks_button_brightness() -> None:
    scenario = _load_scenario("journey_of_light.dispatch_all.yaml")

    assert scenario["node"] == SCREEN
    assert scenario["steps"][0]["template"] == (
        "games/wos/events/journey_of_light/references/crop/"
        "main_journey_of_light.dispatch_all.text.png"
    )
    assert scenario["steps"][0]["threshold"] == 0.75
    assert scenario["steps"][0]["min_match_saturation"] == 80
    assert scenario["steps"][0]["min_patch_bright_ratio"] == 0.05


@pytest.mark.asyncio
async def test_dispatch_and_claim_templates_are_distinct() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    rules = [
        {
            "name": "dispatch",
            "region": "journey_of_light.dispatch_all",
            "action": "findIcon",
            "threshold": 0.75,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "main_journey_of_light.dispatch_all.text.png"
            ),
        },
        {
            "name": "claim",
            "region": "journey_of_light.claim_all",
            "action": "findIcon",
            "threshold": 0.75,
            "template": "games/wos/core/common/references/crop/claim_all_claim_all.png",
        },
    ]

    dispatch_out = await evaluate_overlay_rules_async(
        _load_bgr("main.png"), area_doc, REPO_ROOT, rules, current_screen=SCREEN
    )
    claim_out = await evaluate_overlay_rules_async(
        _claim_all_frame(), area_doc, REPO_ROOT, rules, current_screen=SCREEN
    )

    assert dispatch_out["dispatch"]["matched"] is True
    assert dispatch_out["claim"]["matched"] is False
    assert claim_out["dispatch"]["matched"] is False
    assert claim_out["claim"]["matched"] is True


@pytest.mark.asyncio
async def test_journey_of_light_red_dot_pushes_quick_adventure_scenario() -> None:
    frame = _load_bgr("main.png")
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.add.has_red_dot")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen=SCREEN,
    )

    hit = out["journey_of_light.add.has_red_dot"]
    assert hit["matched"] is True
    assert hit["red_dot_present"] is True
    assert hit["pushScenario"] == [
        {
            "type": "journey_of_light",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 60,
        }
    ]


@pytest.mark.asyncio
async def test_my_treasures_red_dot_pushes_enable_scenario() -> None:
    frame = _load_bgr("my_treasures.png")
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.my_treasures.has_red_dot")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen=TREASURES_SCREEN,
    )

    hit = out["journey_of_light.my_treasures.has_red_dot"]
    assert hit["matched"] is True
    assert hit["red_dot_present"] is True
    assert hit["pushScenario"] == [
        {
            "type": "journey_of_light.my_treasures",
            "dsl_scenario": None,
            "priority": None,
            "ttl": 60,
        }
    ]


@pytest.mark.asyncio
async def test_my_treasures_red_dot_pushes_even_when_screen_is_stale_deals() -> None:
    frame = _load_bgr("my_treasures.png")
    area_doc = load_area_doc(REPO_ROOT)
    rule = _analyze_rule("journey_of_light.my_treasures.has_red_dot")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="deals",
    )

    hit = out["journey_of_light.my_treasures.has_red_dot"]
    assert hit["matched"] is True
    assert hit["red_dot_present"] is True
    assert hit["pushScenario"][0]["type"] == "journey_of_light.my_treasures"
    assert hit["pushScenario"][0]["priority"] is None


def test_my_treasures_scenario_clicks_enable_buttons() -> None:
    scenario = _load_scenario("journey_of_light.my_treasures.yaml")

    assert scenario["node"] == TREASURES_SCREEN
    assert scenario["priority"] == 85000
    assert "tapanywhereyoexit" not in str(scenario["steps"])
    assert [step["while_match"] for step in scenario["steps"]] == [
        "journey_of_light.my_treasures.enable.common",
        "journey_of_light.my_treasures.enable.premium",
    ]
    assert [step["steps"][0]["click"] for step in scenario["steps"]] == [
        "journey_of_light.my_treasures.enable.common",
        "journey_of_light.my_treasures.enable.premium",
    ]
    for step in scenario["steps"]:
        assert step["threshold"] == 0.85
        assert step["min_match_saturation"] == 80
        assert step["min_patch_bright_ratio"] == 0.05
        confirm = step["steps"][2]
        assert confirm["while_match"] == "journey_of_light.my_treasures.enable.popup"
        assert confirm["template"] == (
            "games/wos/events/journey_of_light/references/crop/"
            "my_treasures_enable_popup_journey_of_light.my_treasures.enable.popup.png"
        )
        assert confirm["steps"][0]["click"] == "journey_of_light.my_treasures.enable.popup"
        assemble = step["steps"][3]
        assert assemble["while_match"] == "journey_of_light.my_treasures.assemble_popup.start"
        assert assemble["template"] == (
            "games/wos/events/journey_of_light/references/crop/"
            "my_treasures_assemble_popup_journey_of_light.my_treasures."
            "assemble_popup.start.png"
        )
        assert assemble["steps"][0]["click"] == (
            "journey_of_light.my_treasures.assemble_popup.checkbox"
        )
        assert assemble["steps"][2]["click"] == (
            "journey_of_light.my_treasures.assemble_popup.start"
        )
    assert "click: journey_of_light.my_treasures.assemble_popup.assemble_now" not in (
        (MODULE_DIR / "scenarios" / "journey_of_light.my_treasures.yaml")
        .read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_my_treasures_enable_templates_reject_disabled_buttons() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    rules = [
        {
            "name": "common",
            "region": "journey_of_light.my_treasures.enable.common",
            "action": "findIcon",
            "threshold": 0.85,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "my_treasures_journey_of_light.my_treasures.enable.common.png"
            ),
            "min_match_saturation": 80,
            "min_patch_bright_ratio": 0.05,
        },
        {
            "name": "premium",
            "region": "journey_of_light.my_treasures.enable.premium",
            "action": "findIcon",
            "threshold": 0.85,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "my_treasures_journey_of_light.my_treasures.enable.premium.png"
            ),
            "min_match_saturation": 80,
            "min_patch_bright_ratio": 0.05,
        },
    ]

    enabled_out = await evaluate_overlay_rules_async(
        _load_bgr("my_treasures.png"),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )
    disabled_out = await evaluate_overlay_rules_async(
        _disabled_my_treasures_enable_frame(),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )

    assert enabled_out["common"]["matched"] is True
    assert enabled_out["premium"]["matched"] is True
    assert disabled_out["common"]["matched"] is False
    assert disabled_out["premium"]["matched"] is False


@pytest.mark.asyncio
async def test_my_treasures_popup_enable_template_matches_only_popup() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    rules = [
        {
            "name": "popup_enable",
            "region": "journey_of_light.my_treasures.enable.popup",
            "action": "findIcon",
            "threshold": 0.85,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "my_treasures_enable_popup_journey_of_light.my_treasures.enable.popup.png"
            ),
            "min_match_saturation": 80,
            "min_patch_bright_ratio": 0.05,
        }
    ]

    popup_out = await evaluate_overlay_rules_async(
        _load_popup_bgr(),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )
    page_out = await evaluate_overlay_rules_async(
        _load_bgr("my_treasures.png"),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )

    assert popup_out["popup_enable"]["matched"] is True
    assert page_out["popup_enable"]["matched"] is False


@pytest.mark.asyncio
async def test_my_treasures_assemble_popup_buttons_are_labeled() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    rules = [
        {
            "name": "assemble_now",
            "region": "journey_of_light.my_treasures.assemble_popup.assemble_now",
            "action": "findIcon",
            "threshold": 0.85,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "my_treasures_assemble_popup_journey_of_light.my_treasures."
                "assemble_popup.assemble_now.png"
            ),
            "min_match_saturation": 80,
            "min_patch_bright_ratio": 0.05,
        },
        {
            "name": "start",
            "region": "journey_of_light.my_treasures.assemble_popup.start",
            "action": "findIcon",
            "threshold": 0.85,
            "template": (
                "games/wos/events/journey_of_light/references/crop/"
                "my_treasures_assemble_popup_journey_of_light.my_treasures."
                "assemble_popup.start.png"
            ),
            "min_match_saturation": 80,
            "min_patch_bright_ratio": 0.05,
        },
    ]

    popup_out = await evaluate_overlay_rules_async(
        _load_assemble_popup_bgr(),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )
    page_out = await evaluate_overlay_rules_async(
        _load_bgr("my_treasures.png"),
        area_doc,
        REPO_ROOT,
        rules,
        current_screen=TREASURES_SCREEN,
    )

    assert popup_out["assemble_now"]["matched"] is True
    assert popup_out["start"]["matched"] is True
    assert page_out["assemble_now"]["matched"] is False
    assert page_out["start"]["matched"] is False


def test_journey_of_light_has_own_screen_verify() -> None:
    screen_graph.invalidate_screen_verify_config()
    try:
        expected_journey = [
            {
                "match": "journey_of_light.journey_of_light.title",
                "tab_active": "journey_of_light.journey_of_light.title",
                "threshold": 0.9,
            }
        ]
        expected_treasures = [
            {
                "match": "journey_of_light.my_treasures.title",
                "tab_active": "journey_of_light.my_treasures.title",
                "threshold": 0.9,
            }
        ]
        assert screen_graph.screen_verify_rules(SCREEN) == expected_journey
        assert screen_graph.screen_landmark_rules(SCREEN) == expected_journey
        assert screen_graph.screen_verify_rules(TREASURES_SCREEN) == expected_treasures
        assert screen_graph.screen_landmark_rules(TREASURES_SCREEN) == expected_treasures
        assert screen_graph.screen_verify_rules("event.journey_of_light") == []
    finally:
        screen_graph.invalidate_screen_verify_config()


@pytest.mark.asyncio
async def test_deals_routes_to_journey_of_light_screen() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            "deals",
            SCREEN,
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (
                SCREEN,
                [{"type": "tab_index", "region": "deals.tabs_strip", "index": 0}],
            )
        ]
        assert screen_graph.route_taps(SCREEN, TREASURES_SCREEN) == [
            ["journey_of_light.my_treasures.title"]
        ]
        assert screen_graph.route_taps(TREASURES_SCREEN, SCREEN) == [
            ["journey_of_light.journey_of_light.title"]
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


@pytest.mark.asyncio
async def test_stale_deals_routes_directly_to_my_treasures_tab() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            "deals",
            TREASURES_SCREEN,
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (TREASURES_SCREEN, ["journey_of_light.my_treasures.title"]),
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()
