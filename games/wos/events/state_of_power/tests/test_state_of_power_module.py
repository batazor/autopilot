from __future__ import annotations

from pathlib import Path

import yaml


MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    return yaml.safe_load((MODULE_DIR / rel).read_text(encoding="utf-8"))


def test_state_of_power_area_marks_matchmaking_ttl() -> None:
    area = _load_yaml("area.yaml")
    screens = area.get("screens") or []
    main = next(screen for screen in screens if screen.get("screen_id") == "event.state_of_power")
    regions = {region["name"]: region for region in main.get("regions") or []}

    assert main["ocr"] == "references/main.png"
    assert "event.state_of_power" in regions
    ttl = regions["state_of_power.matchmaking.ttl"]
    assert ttl["action"] == "text"
    assert ttl["type"] == "time"
    assert ttl["bbox"]["original_width"] == 720
    assert ttl["bbox"]["original_height"] == 1280


def test_state_of_power_scenario_persists_matchmaking_timer() -> None:
    scenario = _load_yaml("scenarios/event.state_of_power.yaml")

    assert scenario["node"] == "event.state_of_power"
    assert scenario["cond"] == "active_player != null"
    assert {
        "ocr": "state_of_power.matchmaking.ttl",
        "event_timer": "state_of_power.matchmaking",
    } in scenario["steps"]
