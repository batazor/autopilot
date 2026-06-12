"""Labyrinth module wiring."""
from __future__ import annotations

from pathlib import Path

import yaml

from navigation import screen_graph

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE = (
    "games/wos/events/labyrinth/references/crop/main_city_main_city.to.labyrinth.png"
)


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


def test_labyrinth_analyzer_uses_existing_main_city_icon_template() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    rule = rules["labyrinth.main_city.event_icon.visible"]

    assert rule["region"] == "main_city.icon_search"
    assert rule["template"] == TEMPLATE
    assert (REPO_ROOT / TEMPLATE).is_file()


def test_main_city_routes_to_labyrinth_by_event_button() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        assert screen_graph.route_taps("main_city", "event.labyrinth") == [
            ["main_city.to.labyrinth"]
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
    finally:
        screen_graph.invalidate_screen_verify_config()
