from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from navigation import screen_graph

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_manifest_declares_romance_season_module() -> None:
    manifest = _load_yaml("module.yaml")

    assert manifest["id"] == "romance_season"
    assert manifest["enabled"] is True
    assert manifest["area"] == "area.yaml"
    assert manifest["scenarios"] == "scenarios"
    assert manifest["analyze"] == "analyze/analyze.yaml"
    assert manifest["references"] == "references"


def test_area_marks_event_state_regions() -> None:
    area = _load_yaml("area.yaml")
    screens = area["screens"]
    event = next(s for s in screens if s.get("screen_id") == "event.romance_season")
    target = next(s for s in screens if s.get("screen_id") == "romance_season.target")
    rosarion = next(
        s for s in screens if s.get("screen_id") == "romance_season.rosarion"
    )
    regions = {r["name"]: r for r in event.get("regions") or []}

    assert event["screen_region"] == "romance_season.title"
    assert regions["romance_season.go"]["has_red_dot"] is True
    assert regions["romance_season.attack_count"]["action"] == "text"
    assert regions["romance_season.attack_count"]["type"] == "integer"
    assert regions["romance_season.ttl"]["action"] == "text"
    assert regions["romance_season.ttl"]["type"] == "time"
    assert target["screen_region"] == "romance_season.attack"
    assert rosarion["screen_region"] == "rosarion.title"


def test_scenario_persists_attempts_and_ttl_then_opens_rosarion() -> None:
    scenario = _load_yaml("scenarios/event.romance_season.yaml")

    assert scenario["node"] == "event.romance_season"
    assert scenario["cond"] == "active_player != null"
    assert scenario["steps"] == [
        {
            "ocr": "romance_season.attack_count",
            "type": "integer",
            "store": "romance_season.attack_count",
            "state": "events.romanceSeason.attack_count",
        },
        {
            "ocr": "romance_season.ttl",
            "type": "time",
            "store": "romance_season.ttl",
            "state": "events.romanceSeason.ttl_remaining_s",
            "event_timer": "romance_season",
        },
        {"click": "romance_season.go"},
        {
            "wait_screen": {
                "any": ["romance_season.target", "main_world"],
                "max": 5,
                "interval": "500ms",
            }
        },
        {"click": "romance_season.attack"},
        {
            "wait_screen": {
                "any": ["romance_season.rosarion"],
                "max": 10,
                "interval": "500ms",
            }
        },
    ]


def test_analyzer_pushes_event_from_go_red_dot_with_ttl() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rule = analyze["overlay"][0]

    assert rule["name"] == "romance_season.go.has_red_dot"
    assert rule["region"] == "romance_season.go"
    assert rule["isRedDot"] is True
    assert rule["screens"] == ["event.romance_season"]
    assert rule["ttl"] == "1m"
    assert rule["steps"] == [
        {"push_scenario": {"name": "event.romance_season", "ttl": "1m"}}
    ]


def test_routes_and_screen_verify_are_registered() -> None:
    screen_graph.invalidate_edge_taps_cache()
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        assert screen_graph.route_taps("main_city", "event.romance_season", game="wos") == [
            ["main_city.to.romance_season"]
        ]
        assert screen_graph.route_taps("event.romance_season", "main_city", game="wos") == [
            ["icon.page.back"]
        ]
        assert screen_graph.route_taps(
            "event.romance_season", "romance_season.target", game="wos"
        ) == [["romance_season.go"]]
        assert screen_graph.route_taps(
            "romance_season.target", "romance_season.rosarion", game="wos"
        ) == [["romance_season.attack"]]
        assert screen_graph.route_taps(
            "romance_season.rosarion", "main_city", game="wos"
        ) == [["icon.page.back"], ["main_world.to.main_city"]]
        assert screen_graph.screen_verify_rules("event.romance_season") == [
            {"ocr": "romance_season.title", "contains": "Romance Season"}
        ]
        assert screen_graph.screen_verify_rules("romance_season.target") == [
            {"match": "romance_season.attack", "threshold": 0.9}
        ]
        assert screen_graph.screen_verify_rules("romance_season.rosarion") == [
            {"ocr": "rosarion.title", "contains": "Rosarion"}
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_main_reference_matches_title_and_go_red_dot() -> None:
    frame = cv2.imread(str(MODULE_DIR / "references" / "main.png"))
    assert frame is not None
    rules = [
        {
            "name": "romance_season.title.visible",
            "action": "findIcon",
            "region": "romance_season.title",
            "threshold": 0.9,
        },
        {
            "name": "romance_season.go.has_red_dot",
            "action": "findIcon",
            "region": "romance_season.go",
            "threshold": 0.9,
            "isRedDot": True,
        },
    ]

    out = await evaluate_overlay_rules_async(
        frame,
        load_area_doc(REPO_ROOT, game="wos"),
        REPO_ROOT,
        rules,
        current_screen="event.romance_season",
    )

    assert out["romance_season.title.visible"]["matched"] is True
    assert out["romance_season.go.has_red_dot"]["matched"] is True
    assert out["romance_season.go.has_red_dot"]["red_dot_present"] is True


@pytest.mark.asyncio
async def test_attack_and_rosarion_references_match_new_nodes() -> None:
    area_doc = load_area_doc(REPO_ROOT, game="wos")

    attack_frame = cv2.imread(str(MODULE_DIR / "references" / "attack.png"))
    assert attack_frame is not None
    attack_out = await evaluate_overlay_rules_async(
        attack_frame,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "romance_season.attack.visible",
                "action": "findIcon",
                "region": "romance_season.attack",
                "threshold": 0.9,
            }
        ],
        current_screen="romance_season.target",
    )
    assert attack_out["romance_season.attack.visible"]["matched"] is True

    rosarion_frame = cv2.imread(str(MODULE_DIR / "references" / "rosarion.png"))
    assert rosarion_frame is not None
    rosarion_out = await evaluate_overlay_rules_async(
        rosarion_frame,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "rosarion.title.visible",
                "action": "findIcon",
                "region": "rosarion.title",
                "threshold": 0.9,
            }
        ],
        current_screen="romance_season.rosarion",
    )
    assert rosarion_out["rosarion.title.visible"]["matched"] is True


def test_rosarion_can_reuse_deploy_layout_controls() -> None:
    area_doc = load_area_doc(REPO_ROOT, game="wos")

    assert screen_region_by_name(area_doc, "rosarion.title")[0]["screen_id"] == (
        "romance_season.rosarion"
    )
    for region_name in (
        "heroes.deploy.equalize",
        "heroes.deploy.balance",
        "heroes.deploy.ttl",
        "heroes.deploy.deploy",
    ):
        found = screen_region_by_name(area_doc, region_name)
        assert found is not None, region_name
        assert found[0]["screen_id"] == "heroes.deploy"
