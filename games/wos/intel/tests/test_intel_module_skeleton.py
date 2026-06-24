"""Structural checks for the intel module."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation.screen_graph import route_taps

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_module_manifest_declares_intel() -> None:
    manifest = _load_yaml("module.yaml")
    assert manifest["id"] == "intel"
    assert manifest["title"] == "Intel"
    assert manifest["enabled"] is True


def test_area_declares_intel_screen_and_fight_region() -> None:
    area = _load_yaml("area.yaml")
    assert area.get("version") == 2
    screens = area.get("screens") or []
    by_id = {screen["screen_id"]: screen for screen in screens if screen["screen_id"] != "intel"}
    intel_screens = [screen for screen in screens if screen["screen_id"] == "intel"]
    screen = next(
        screen for screen in intel_screens if screen.get("screen_region") == "intel.title"
    )
    assert screen["screen_id"] == "intel"
    assert screen["screen_region"] == "intel.title"
    regions = {r["name"]: r for r in screen["regions"]}
    assert regions["intel.title"]["action"] == "text"
    assert regions["intel.fight"]["action"] == "exist"
    claim_screen = next(
        screen for screen in intel_screens if screen.get("ocr") == "references/claim.png"
    )
    claim_regions = {r["name"]: r for r in claim_screen["regions"]}
    assert claim_regions["intel.claim_all"]["action"] == "exist"
    assert by_id["intel.fight"]["screen_region"] == "intel.fight.view"
    assert by_id["intel.explore"]["screen_region"] == "intel.explore"
    attack_regions = {
        r["name"]: r
        for r in by_id["main_world"]["regions"]
        if r["name"].startswith("intel.")
    }
    assert attack_regions["intel.attack"]["action"] == "exist"


def test_analyze_has_no_overlay_rules() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    assert analyze.get("overlay") == []


def test_lighthouse_scenario_taps_fight_marker() -> None:
    scenario = _load_yaml("scenarios/intel_lighthouse.yaml")
    assert scenario["enabled"] is True
    assert scenario["node"] == "intel"
    steps = scenario["steps"]
    assert steps[0]["while_match"] == "intel.claim_all"
    assert steps[0]["max"] == 1
    claim_steps = steps[0]["steps"]
    assert {"click": "intel.claim_all"} in claim_steps
    assert any(step.get("while_match") == "button.click_to_continue" for step in claim_steps)
    assert any(step.get("while_match") == "button.tap_anywhere_to_exit" for step in claim_steps)
    assert steps[1]["exec"] == "tap_intel_fight"
    assert steps[1]["threshold"] == 0.72
    assert steps[2]["wait_screen"]["any"] == ["intel.fight"]
    assert steps[3] == {"click": "intel.fight.view"}
    assert steps[4]["wait_screen"]["any"] == ["intel.explore", "main_world"]
    assert steps[5] == {"wait": "1s"}
    assert steps[6] == {
        "match": "intel.explore.is_blue",
        "steps": [{"click": "intel.explore"}],
        "else": [{"click": "intel.attack"}],
    }
    assert steps[7]["wait_screen"]["any"] == ["heroes.deploy", "squad_settings"]
    deploy_branch = steps[8]
    assert deploy_branch["cond"] == "currentNode == heroes.deploy"
    assert deploy_branch["steps"] == [
        {"click": "heroes.deploy.equalize"},
        {"wait": "500ms"},
        {
            "ocr": "heroes.deploy.ttl",
            "type": "string",
            "store": "intel.march_ttl",
        },
        {"click": "heroes.deploy.deploy"},
        {"wait": "1s"},
        {
            "exec": "confirm_intel_march_lease",
            "ttl_field": "intel.march_ttl",
            "round_trip_multiplier": 2,
            "extra_seconds": 15,
        },
    ]
    squad_branch = steps[9]
    assert squad_branch["cond"] == "currentNode == squad_settings"
    squad_steps = squad_branch["steps"]
    assert {"click": "squad_settings.quick_deploy"} in squad_steps
    assert {"click": "squad_settings.fight"} in squad_steps
    assert not any("push_scenario" in str(step) for step in steps)


def test_intel_run_uses_real_stamina_flow() -> None:
    lighthouse = _load_yaml("scenarios/intel_lighthouse.yaml")
    scenario = _load_yaml("scenarios/intel_run.yaml")

    assert scenario["enabled"] is True
    assert scenario["node"] == "intel"
    assert scenario["steps"] == lighthouse["steps"]
    assert scenario["steps"] != [{"wait": "1s"}]


def test_intel_route_is_reachable_from_world_map() -> None:
    assert route_taps("main_city", "intel", game="wos") == [
        ["main_city.to.world"],
        ["main_world.to.intel"],
    ]
    assert route_taps("intel", "main_city", game="wos") == [
        ["icon.page.back"],
        ["main_world.to.main_city"],
    ]
    assert route_taps("announcements", "intel", game="wos") == [
        ["announcements.back"],
        ["main_world.to.intel"],
    ]


@pytest.mark.asyncio
async def test_claim_all_region_detected_on_claim_reference() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    image = cv2.imread(str(MODULE_DIR / "references" / "claim.png"))
    assert image is not None

    rule = {
        "name": "intel.claim_all.visible",
        "region": "intel.claim_all",
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        image,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="intel",
    )

    assert out["intel.claim_all.visible"]["matched"] is True


@pytest.mark.asyncio
async def test_attack_region_detected_on_attack_reference() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    image = cv2.imread(str(MODULE_DIR / "references" / "attack.png"))
    assert image is not None

    rule = {
        "name": "intel.attack.visible",
        "region": "intel.attack",
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        image,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="main_world",
    )

    assert out["intel.attack.visible"]["matched"] is True


@pytest.mark.asyncio
async def test_explore_button_color_discriminates_blue_vs_attack() -> None:
    # The intel_run branch picks Explore vs Attack by the button colour: the blue
    # Explore button (exploration target) must match `intel.explore.is_blue`, the
    # orange Attack button (combat target) must NOT. Colour survives scrcpy H.264
    # where the small title OCR does not.
    area_doc = load_area_doc(REPO_ROOT)
    rule = {
        "name": "intel.explore.is_blue.check",
        "region": "intel.explore.is_blue",
        "action": "color_check",
        "type": "blue",
        "threshold": 0.5,
    }
    explore = cv2.imread(str(MODULE_DIR / "references" / "explore.png"))
    attack = cv2.imread(str(MODULE_DIR / "references" / "attack.png"))
    assert explore is not None and attack is not None

    out_explore = await evaluate_overlay_rules_async(
        explore, area_doc, REPO_ROOT, [rule], current_screen="intel.explore"
    )
    out_attack = await evaluate_overlay_rules_async(
        attack, area_doc, REPO_ROOT, [rule], current_screen="main_world"
    )

    assert out_explore["intel.explore.is_blue.check"]["matched"] is True
    assert out_attack["intel.explore.is_blue.check"]["matched"] is False


def _load_exec_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "intel_exec_test",
        MODULE_DIR / "exec.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_detect_fight_markers_finds_blue_and_purple_fixture_icons() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "main.png"))
    assert image is not None

    markers = mod.detect_fight_markers(image)

    assert len(markers) == 7
    centers = sorted((m.center.x, m.center.y) for m in markers)
    expected = [
        (100, 539),
        (128, 632),
        (290, 721),
        (348, 841),
        (427, 346),
        (544, 415),
        (618, 629),
    ]
    for got, want in zip(centers, expected, strict=True):
        assert abs(got[0] - want[0]) <= 6
        assert abs(got[1] - want[1]) <= 6


def test_detect_intel_markers_finds_skull_fixture_icons() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "claim.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)

    skull_centers = sorted(
        (m.center.x, m.center.y) for m in markers if m.kind == "skull"
    )
    expected = [
        (175, 358),
        (188, 676),
        (361, 741),
        (404, 845),
    ]
    assert len(skull_centers) == len(expected)
    for got, want in zip(skull_centers, expected, strict=True):
        assert abs(got[0] - want[0]) <= 6
        assert abs(got[1] - want[1]) <= 6


def test_detect_intel_markers_finds_horned_skull_fixture_icon() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)

    horned = [m for m in markers if m.kind == "skull_horned"]
    assert len(horned) == 1
    assert abs(horned[0].center.x - 439) <= 6
    assert abs(horned[0].center.y - 459) <= 6


def test_detect_intel_markers_finds_camp_fixture_icon() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)

    camps = [m for m in markers if m.kind == "camp"]
    assert len(camps) == 1
    assert abs(camps[0].center.x - 468) <= 6
    assert abs(camps[0].center.y - 596) <= 6


def test_detect_intel_markers_finds_special_event_icons() -> None:
    # The special-event Intel skin (references/main_special.png) uses new marker
    # art: an orange tent (camp), a paw print (beast), and purple crossed axes
    # (fight). All three logical kinds must be detected so the existing intel_run
    # scenario taps them with no scenario change.
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "main_special.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)
    kinds = {m.kind for m in markers}
    assert {"camp", "fight", "beast"} <= kinds

    def _near(kind: str, cx: int, cy: int) -> bool:
        return any(
            m.kind == kind and abs(m.center.x - cx) <= 8 and abs(m.center.y - cy) <= 8
            for m in markers
        )

    # The three crop-source pins match at ~1.0 confidence — stable anchors.
    assert _near("camp", 142, 375)   # tent
    assert _near("beast", 606, 674)  # paw print
    assert _near("fight", 410, 829)  # crossed axes


def test_pick_marker_prioritizes_gold_intel_icons() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)
    picked = mod._pick_marker(markers, "best_score")

    assert picked is not None
    assert picked.kind in {"skull_horned", "camp"}
    assert picked.color == "gold"


def test_pick_marker_prioritizes_color_before_type_and_score() -> None:
    mod = _load_exec_module()
    gold_fight = mod.IntelMarker(
        x=0,
        y=0,
        w=10,
        h=10,
        score=0.70,
        kind="fight",
        color="gold",
    )
    purple_special = mod.IntelMarker(
        x=20,
        y=0,
        w=10,
        h=10,
        score=1.00,
        kind="skull_horned",
        color="purple",
    )

    assert mod._pick_marker([purple_special, gold_fight], "best_score") is gold_fight


def test_pick_marker_prioritizes_type_before_score_inside_color() -> None:
    mod = _load_exec_module()
    gold_special = mod.IntelMarker(
        x=0,
        y=0,
        w=10,
        h=10,
        score=0.70,
        kind="skull_horned",
        color="gold",
    )
    gold_regular = mod.IntelMarker(
        x=20,
        y=0,
        w=10,
        h=10,
        score=1.00,
        kind="fight",
        color="gold",
    )

    assert mod._pick_marker([gold_regular, gold_special], "best_score") is gold_special
