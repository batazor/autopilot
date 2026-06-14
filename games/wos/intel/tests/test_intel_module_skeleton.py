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
    assert steps[4]["wait_screen"]["any"] == ["intel.explore"]
    assert steps[5] == {"click": "intel.explore"}
    assert steps[6]["wait_screen"]["any"] == ["squad_settings"]
    assert {"click": "squad_settings.quick_deploy"} in steps
    assert {"click": "squad_settings.fight"} in steps
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
        ["icon.world"],
        ["main_world.to.intel"],
    ]
    assert route_taps("intel", "main_city", game="wos") == [
        ["icon.page.back"],
        ["button.to_main_city"],
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


def test_pick_marker_prioritizes_gold_intel_icons() -> None:
    mod = _load_exec_module()
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None

    markers = mod.detect_intel_markers(image)
    picked = mod._pick_marker(markers, "best_score")

    assert picked is not None
    assert picked.kind in {"skull_horned", "camp"}
