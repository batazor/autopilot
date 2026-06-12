"""Labyrinth module wiring."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from layout.white_border_detector import (
    find_white_border_match_in_search_roi,
    has_white_border_in_bbox_percent,
)
from navigation import main_menu_panel_resolver, screen_graph  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE = (
    "games/wos/events/labyrinth/references/crop/main_city_main_city.to.labyrinth.png"
)
CAVES = {
    "event.labyrinth.land_of_heroes": {
        "route": "labyrinth.to.land_of_heroes",
        "slot": "left",
        "title": "Land of Heroes",
        "days": ["Monday", "Tuesday"],
    },
    "event.labyrinth.cave_of_monsters": {
        "route": "labyrinth.to.cave_of_monsters",
        "slot": "left",
        "title": "Cave of Monsters",
        "days": ["Wednesday", "Thursday"],
    },
    "event.labyrinth.charm_mine": {
        "route": "labyrinth.to.charm_mine",
        "slot": "right",
        "title": "Charm Mine",
        "days": ["Wednesday", "Thursday"],
    },
    "event.labyrinth.research_center": {
        "route": "labyrinth.to.research_center",
        "slot": "left",
        "title": "Research Center",
        "days": ["Friday", "Saturday"],
    },
    "event.labyrinth.gear_forge": {
        "route": "labyrinth.to.gear_forge",
        "slot": "right",
        "title": "Gear Forge",
        "days": ["Friday", "Saturday"],
    },
    "event.labyrinth.gaia_heart": {
        "route": "labyrinth.to.gaia_heart",
        "slot": "bottom",
        "title": "Gaia Heart",
        "days": ["Sunday"],
    },
}
SLOT_TIMERS = {
    "left": "labyrinth.left_cave.ttl",
    "right": "labyrinth.right_cave.ttl",
    "bottom": "labyrinth.bottom_cave.ttl",
}
CAVE_RATIOS = {
    "event.labyrinth.land_of_heroes": ["50", "20", "30"],
    "event.labyrinth.cave_of_monsters": ["50", "10", "40"],
    "event.labyrinth.charm_mine": ["50", "10", "40"],
    "event.labyrinth.research_center": ["50", "10", "40"],
    "event.labyrinth.gear_forge": ["50", "10", "40"],
    "event.labyrinth.gaia_heart": ["50", "20", "30"],
}


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_labyrinth_declares_main_city_event_button() -> None:
    area = _load_yaml("area.yaml")
    regions = {
        region["name"]: region
        for screen in area["screens"]
        for region in screen.get("regions", [])
    }

    assert regions["main_city.to.labyrinth"]["action"] == "exist"
    assert regions["labyrinth.title"]["action"] == "text"
    assert regions["labyrinth.title"]["type"] == "string"
    assert regions["labyrinth.reward_track"]["action"] == "exist"
    assert regions["labyrinth.cave.title"]["action"] == "text"
    assert regions["labyrinth.cave.title"]["type"] == "string"
    assert regions["labyrinth.cave.challenge"]["action"] == "click"
    assert regions["labyrinth.squad.title"]["action"] == "text"
    assert regions["labyrinth.squad.balance"]["action"] == "click"
    assert regions["labyrinth.squad.deploy"]["action"] == "click"
    assert regions["labyrinth.balance.infantry.percent"]["action"] == "click"
    assert regions["labyrinth.balance.lancer.percent"]["action"] == "click"
    assert regions["labyrinth.balance.marksman.percent"]["action"] == "click"
    assert regions["labyrinth.balance.all_future.checkbox"]["action"] == "click"
    assert regions["labyrinth.balance.all_future.checked"]["action"] == "color_check"
    assert regions["labyrinth.balance.all_future.checked"]["type"] == "green"
    assert regions["labyrinth.balance.confirm"]["action"] == "click"
    for cave in CAVES.values():
        assert regions[cave["route"]]["action"] == "exist"

    for slot in SLOT_TIMERS:
        title_region = f"labyrinth.{slot}_cave.title"
        timer_region = f"labyrinth.{slot}_cave.ttl"
        assert regions[title_region]["action"] == "text"
        assert regions[title_region]["type"] == "string"
        assert regions[timer_region]["action"] == "text"
        assert regions[timer_region]["type"] == "time"
    assert regions["labyrinth.bottom_cave.status"]["action"] == "text"
    assert regions["labyrinth.bottom_cave.status"]["type"] == "string"


def test_labyrinth_analyzer_uses_existing_main_city_icon_template() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    rule = rules["labyrinth.main_city.event_icon.visible"]

    assert rule["region"] == "main_city.icon_search"
    assert rule["template"] == TEMPLATE
    assert (REPO_ROOT / TEMPLATE).is_file()

    sync_rule = rules["labyrinth.main.visible"]
    assert sync_rule["region"] == "labyrinth.title"
    assert sync_rule["action"] == "text"
    assert sync_rule["expected"] == ["Labyrinth"]
    assert sync_rule["screens"] == ["event.labyrinth"]
    assert sync_rule["steps"] == [
        {"push_scenario": {"name": "event.labyrinth.sync_caves", "ttl": "5m"}}
    ]

    for cave_node in CAVES:
        cave_slug = cave_node.rsplit(".", 1)[1]
        menu_rule = rules[f"labyrinth.main_menu.{cave_slug}.claimable"]
        assert menu_rule["region"] == "main_menu.city"
        assert menu_rule["action"] == "findIcon"
        assert menu_rule["screens"] == ["main_menu"]
        assert (
            menu_rule["cond"]
            == f"main_menu.panel.labyrinth.{cave_slug}.isClaimable == true"
        )
        assert menu_rule["steps"] == [
            {"push_scenario": {"name": "event.labyrinth", "ttl": "1m"}}
        ]


def test_main_city_routes_to_labyrinth_by_event_button() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        assert screen_graph.route_taps("main_city", "event.labyrinth") == [
            ["main_city.to.labyrinth"]
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


@pytest.mark.asyncio
async def test_main_menu_routes_to_labyrinth_by_dynamic_panel_row() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        assert screen_graph.route_taps("main_menu", "event.labyrinth") is None
        hops = await screen_graph.route_hops_async(
            "main_menu",
            "event.labyrinth",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (
                "event.labyrinth",
                [
                    {
                        "type": "main_menu_panel_row",
                        "section": "labyrinth",
                        "rows": [
                            "land_of_heroes",
                            "cave_of_monsters",
                            "charm_mine",
                            "research_center",
                            "gear_forge",
                            "gaia_heart",
                        ],
                        "approval_region": "main_menu.panel.labyrinth",
                    }
                ],
            )
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


def test_main_menu_scanner_classifies_every_labyrinth_cave_title() -> None:
    from games.wos.core.main_menu.exec import _section_for_row

    for cave_node, cave in CAVES.items():
        cave_slug = cave_node.rsplit(".", 1)[1]
        assert _section_for_row(cave["title"], "", "") == ("labyrinth", cave_slug)


def test_labyrinth_routes_to_cave_nodes() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        for cave_node, cave in CAVES.items():
            assert screen_graph.route_taps("event.labyrinth", cave_node) == [
                [cave["route"]]
            ]
            assert screen_graph.route_taps(cave_node, "event.labyrinth") == [
                ["icon.page.back"]
            ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


def test_labyrinth_screen_verify_uses_title() -> None:
    screen_graph.invalidate_screen_verify_config()
    try:
        expected = [
            {
                "ocr": "labyrinth.title",
                "contains": "Labyrinth",
                "threshold": 0.9,
            }
        ]
        assert screen_graph.screen_verify_rules("event.labyrinth") == expected
        assert screen_graph.screen_landmark_rules("event.labyrinth") == expected
        for cave_node, cave in CAVES.items():
            assert screen_graph.screen_verify_rules(cave_node) == [
                {
                    "ocr": "labyrinth.cave.title",
                    "contains": cave["title"],
                    "threshold": 0.85,
                }
            ]
    finally:
        screen_graph.invalidate_screen_verify_config()


def test_labyrinth_registers_every_cave_as_screen_node() -> None:
    screen_graph.invalidate_screen_verify_config()
    try:
        registered = set(screen_graph.screen_verify_screen_names())
        assert set(CAVES).issubset(registered)
    finally:
        screen_graph.invalidate_screen_verify_config()


def test_labyrinth_scenario_claims_white_border_rewards() -> None:
    scenario = _load_yaml("scenarios/event.labyrinth.yaml")
    first_step = scenario["steps"][0]

    assert first_step["while_match"] == "labyrinth.reward_track"
    assert first_step["isWhiteBorder"] is True
    assert first_step["max_aspect"] == 1.5
    assert first_step["max"] == 6
    assert {"click": "labyrinth.reward_track"} in first_step["steps"]


def test_labyrinth_sync_scenario_pushes_caves_with_timer_deadlines() -> None:
    scenario = _load_yaml("scenarios/event.labyrinth.sync_caves.yaml")
    steps = scenario["steps"]

    assert scenario["node"] == "event.labyrinth"
    assert scenario["cond"] == "active_player != null"
    for slot, timer_region in SLOT_TIMERS.items():
        assert {
            "ocr": f"labyrinth.{slot}_cave.title",
            "store": f"labyrinth.{slot}_cave.title",
        } in steps
        assert {"ocr": timer_region, "event_timer": timer_region} in steps

    def _pushes_from(block: dict) -> list[dict]:
        out: list[dict] = []
        for inner in block.get("steps", []):
            if not isinstance(inner, dict):
                continue
            if "push_scenario" in inner:
                out.append(inner["push_scenario"])
            out.extend(_pushes_from(inner))
        return out

    repeat_blocks = [
        step for step in steps if isinstance(step, dict) and step.get("repeat") == 1
    ]
    pushes = [push for block in repeat_blocks for push in _pushes_from(block)]
    for cave_node, cave in CAVES.items():
        assert {
            "name": cave_node,
            "expires": SLOT_TIMERS[cave["slot"]],
        } in pushes

    gaia_blocks = [
        block
        for block in repeat_blocks
        if str(block.get("cond", "")).startswith("labyrinth.bottom_cave.status")
    ]
    assert gaia_blocks
    assert "labyrinth.bottom_cave.status !~ \"Opens in|Locked\"" in str(gaia_blocks)


def test_labyrinth_cave_scenarios_use_cave_nodes_and_generic_claim_flow() -> None:
    percent_regions = [
        "labyrinth.balance.infantry.percent",
        "labyrinth.balance.lancer.percent",
        "labyrinth.balance.marksman.percent",
    ]

    for cave_node in CAVES:
        scenario = _load_yaml(f"scenarios/{cave_node}.yaml")
        steps = scenario["steps"]

        assert scenario["node"] == cave_node
        assert scenario["cond"] == "active_player != null"
        assert steps[0] == {"click": "labyrinth.cave.challenge"}
        assert steps[1] == {"wait": "1s"}
        assert {"click": "labyrinth.squad.balance"} in steps
        assert {"click": "labyrinth.balance.confirm"} in steps
        assert {"click": "labyrinth.squad.deploy"} in steps
        assert {
            "match": "labyrinth.balance.all_future.checked",
            "else": [
                {"click": "labyrinth.balance.all_future.checkbox"},
                {"wait": "500ms"},
            ],
        } in steps
        type_steps = [step["type_text"] for step in steps if "type_text" in step]
        assert type_steps == CAVE_RATIOS[cave_node]
        for region in percent_regions:
            assert {"click": region} in steps
        assert any(step.get("while_match") == "button.claim" for step in steps)
        assert any(step.get("while_match") == "button.claim.big" for step in steps)
        assert "tapanywhereyoexit" not in str(steps)
        assert "button.tap_anywhere_to_exit" in str(steps)


def test_labyrinth_challenge_reference_is_module_local() -> None:
    assert (MODULE_DIR / "references" / "challenge.png").is_file()


def test_labyrinth_declares_full_wiki_cave_schedule() -> None:
    assert {cave["title"] for cave in CAVES.values()} == {
        "Land of Heroes",
        "Cave of Monsters",
        "Charm Mine",
        "Research Center",
        "Gear Forge",
        "Gaia Heart",
    }
    assert CAVES["event.labyrinth.land_of_heroes"]["days"] == ["Monday", "Tuesday"]
    assert CAVES["event.labyrinth.cave_of_monsters"]["days"] == [
        "Wednesday",
        "Thursday",
    ]
    assert CAVES["event.labyrinth.charm_mine"]["days"] == ["Wednesday", "Thursday"]
    assert CAVES["event.labyrinth.research_center"]["days"] == ["Friday", "Saturday"]
    assert CAVES["event.labyrinth.gear_forge"]["days"] == ["Friday", "Saturday"]
    assert CAVES["event.labyrinth.gaia_heart"]["days"] == ["Sunday"]


def test_labyrinth_reward_track_does_not_match_unavailable_fixture_rewards() -> None:
    area = _load_yaml("area.yaml")
    regions = {
        region["name"]: region
        for screen in area["screens"]
        for region in screen.get("regions", [])
    }
    image = cv2.imread(str(MODULE_DIR / "references" / "main.png"))
    assert image is not None

    bbox = regions["labyrinth.reward_track"]["bbox"]
    assert find_white_border_match_in_search_roi(image, bbox, max_aspect=1.5) is None
    assert has_white_border_in_bbox_percent(image, bbox) is False
