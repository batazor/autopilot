"""Tundra Trek module wiring."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph, template_icon_resolver  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE = (
    "games/wos/events/tundra_trek/references/crop/main_city_main_city.to.tundra_trek.png"
)
WORLD_TEMPLATE = (
    "games/wos/events/tundra_trek/references/crop/main_city_main_world.to.tundra_trek.png"
)


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_tundra_trek_manifest_declares_working_module() -> None:
    manifest = _load_yaml("module.yaml")

    assert manifest["id"] == "tundra_trek"
    assert manifest["title"] == "Tundra Trek"
    assert manifest["scenarios"] == "scenarios"
    assert manifest["analyze"] == "analyze/analyze.yaml"
    assert manifest["area"] == "area.yaml"
    assert manifest["references"] == "references"


def test_tundra_trek_declares_icon_entry_regions() -> None:
    area = _load_yaml("area.yaml")
    regions = {
        region["name"]: region
        for screen in area["screens"]
        for region in screen.get("regions", [])
    }

    assert regions["main_city.to.tundra_trek"]["action"] == "exist"
    assert regions["main_world.to.tundra_trek"]["action"] == "exist"
    assert regions["main_city.tundra_trek.icon_search"]["overlay_auxiliary"] is True
    assert regions["main_world.tundra_trek.icon_search"]["overlay_auxiliary"] is True
    assert regions["main_city.tundra_trek.icon_search"]["bbox"] == {
        "x": 0,
        "y": 43.75,
        "width": 100,
        "height": 56.25,
        "rotation": 0,
        "original_width": 720,
        "original_height": 1280,
    }
    assert regions["tundra_trek.title"]["action"] == "click"
    assert regions["tundra_trek.next_stop"]["action"] == "click"
    assert regions["tundra_trek.skip"]["action"] == "click"


def test_tundra_trek_analyzer_uses_icon_and_main_menu_state() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    city_rule = rules["tundra_trek.main_city.icon.visible"]
    world_rule = rules["tundra_trek.main_world.icon.visible"]
    menu_rule = rules["tundra_trek.main_menu.claimable"]

    assert city_rule["region"] == "main_city.to.tundra_trek"
    assert city_rule["template"] == TEMPLATE
    assert city_rule["search_region"] == "main_city.tundra_trek.icon_search"
    assert city_rule["screens"] == ["main_city"]
    assert world_rule["region"] == "main_world.to.tundra_trek"
    assert world_rule["template"] == WORLD_TEMPLATE
    assert world_rule["search_region"] == "main_world.tundra_trek.icon_search"
    assert world_rule["screens"] == ["main_world"]
    assert menu_rule["region"] == "main_menu.city"
    assert menu_rule["screens"] == ["main_menu"]
    assert menu_rule["cond"] == "main_menu.panel.trek.tundra_trek.isClaimable == true"
    assert {"push_scenario": {"name": "event.tundra_trek", "ttl": "1m"}} in menu_rule[
        "steps"
    ]


def test_tundra_trek_scenario_opens_row_and_claims() -> None:
    scenario = _load_yaml("scenarios/event.tundra_trek.yaml")

    assert scenario["enabled"] is True
    assert scenario["node"] == "event.tundra_trek"
    assert scenario["cond"] == "active_player != null"
    assert scenario["steps"][0] == {"click": "tundra_trek.title"}
    assert scenario["steps"][1] == {"wait": "1s"}
    assert scenario["steps"][2] == {
        "while_match": "tundra_trek.next_stop",
        "max": 10,
        "steps": [
            {"click": "tundra_trek.next_stop"},
            {"wait": "2s"},
        ],
    }
    assert scenario["steps"][3] == {
        "while_match": "tundra_trek.skip",
        "max": 1,
        "steps": [
            {"click": "tundra_trek.skip"},
            {"wait": "1s"},
            {"click": "tundra_trek.skip"},
            {"wait": "1s"},
        ],
    }
    assert scenario["steps"][4]["while_match"] == "button.claim"
    assert scenario["steps"][5]["while_match"] == "button.claim.big"


def test_tundra_trek_screen_verify_uses_title() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        expected = [{"match": "tundra_trek.title", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("event.tundra_trek") == expected
        assert screen_graph.screen_landmark_rules("event.tundra_trek") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_tundra_trek_icon_matches_inside_lower_search_region() -> None:
    frame = cv2.imread(str(MODULE_DIR / "references" / "main_city.png"))
    assert frame is not None
    rule = {
        "name": "tundra_trek.main_city.icon.visible",
        "region": "main_city.to.tundra_trek",
        "action": "findIcon",
        "template": TEMPLATE,
        "search_region": "main_city.tundra_trek.icon_search",
        "threshold": 0.9,
    }

    out = await evaluate_overlay_rules_async(
        frame,
        load_area_doc(REPO_ROOT),
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    hit = out[rule["name"]]
    assert hit["matched"] is True
    assert hit["search_region"] == "main_city.tundra_trek.icon_search"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "region", "search_region", "template"),
    [
        (
            "main_city",
            "main_city.to.tundra_trek",
            "main_city.tundra_trek.icon_search",
            TEMPLATE,
        ),
        (
            "main_world",
            "main_world.to.tundra_trek",
            "main_world.tundra_trek.icon_search",
            WORLD_TEMPLATE,
        ),
    ],
)
async def test_tundra_trek_routes_from_city_and_world(
    source: str,
    region: str,
    search_region: str,
    template: str,
) -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            source,
            "event.tundra_trek",
            instance_id="bs1",
            redis_client=None,
            game="wos",
        )
        assert hops == [
            (
                "event.tundra_trek",
                [
                    {
                        "type": "template_icon",
                        "region": region,
                        "template": template,
                        "search_region": search_region,
                        "threshold": 0.9,
                    }
                ],
            )
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()
