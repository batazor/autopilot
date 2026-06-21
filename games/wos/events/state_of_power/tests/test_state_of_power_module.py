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

    # Routes to main_city (reliable), then the exec drives the events carousel —
    # event.state_of_power can't be reached by the node graph.
    assert scenario["node"] == "main_city"
    # player-bound scenario: no redundant active_player guard
    assert "cond" not in scenario
    assert {
        "ocr": "state_of_power.matchmaking.ttl",
        "event_timer": "state_of_power.matchmaking",
    } in scenario["steps"]


def test_state_of_power_scenario_navigates_via_exec() -> None:
    scenario = _load_yaml("scenarios/event.state_of_power.yaml")
    # The Events panel is a swipe-only carousel (no segmentable tab strip), so the
    # first step is the imperative exec that opens it and selects the SoP tab.
    assert scenario["steps"][0] == {"exec": "goto_state_of_power"}


def test_state_of_power_area_declares_title_region() -> None:
    area = _load_yaml("area.yaml")
    main = next(s for s in area["screens"] if s.get("screen_id") == "event.state_of_power")
    regions = {r["name"]: r for r in main.get("regions") or []}

    title = regions["state_of_power.title"]
    assert title["action"] == "text"
    assert title["bbox"]["original_width"] == 720


def test_state_of_power_screen_verify_reads_title_by_ocr() -> None:
    verify = _load_yaml("routes/screen_verify.yaml")
    rules = verify["screens"]["event.state_of_power"]["rules"]
    # OCR the page title (tolerates per-phase banner reskins, and avoids the tab
    # template false-positiving on sibling carousel pages).
    rule = next(r for r in rules if r.get("ocr") == "state_of_power.title")
    assert rule["contains"] == "State of Power"


def test_state_of_power_area_declares_assist_regions() -> None:
    area = _load_yaml("area.yaml")
    main = next(s for s in area["screens"] if s.get("screen_id") == "event.state_of_power")
    regions = {r["name"]: r for r in main.get("regions") or []}

    # The "Assist ally" button is red-dot gated (lit only while tomes remain).
    assert regions["state_of_power.assist_ally"]["has_red_dot"] is True
    for name in (
        "state_of_power.assist_ally",
        "state_of_power.assist_icon",
        "state_of_power.assist_5x",
        "state_of_power.assist_1x",
        "state_of_power.assist_popup.close",
        "state_of_power.field_hospital.close",
    ):
        assert regions[name]["action"] == "exist"
        assert regions[name]["bbox"]["original_width"] == 720


def test_state_of_power_scenario_assists_allies_then_dismisses() -> None:
    scenario = _load_yaml("scenarios/event.state_of_power.yaml")
    steps = scenario["steps"]

    assist = next(s for s in steps if s.get("while_match") == "state_of_power.assist_ally")
    # Gated on the red dot so it only runs (and spends tomes) while tomes remain.
    assert assist["isRedDot"] is True
    icon = next(
        s for s in assist["steps"] if s.get("while_match") == "state_of_power.assist_icon"
    )
    assert any(s.get("while_match") == "state_of_power.assist_5x" for s in icon["steps"])
    assert any(s.get("while_match") == "state_of_power.assist_1x" for s in icon["steps"])
    # Stacked modals are closed via explicit X taps — the popup detector
    # mis-reads the Field Hospital as a captcha, so dismiss_popup can't close it.
    assert any(
        s.get("while_match") == "state_of_power.assist_popup.close" for s in icon["steps"]
    )
    assert any(
        s.get("while_match") == "state_of_power.field_hospital.close" for s in assist["steps"]
    )
    # Returns home afterwards.
    assert {"push_scenario": "check_main_city"} in steps
