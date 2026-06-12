"""Structural checks for the intel module."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import cv2
import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]


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
    by_id = {screen["screen_id"]: screen for screen in screens}
    screen = by_id["intel"]
    assert screen["screen_id"] == "intel"
    assert screen["screen_region"] == "intel.title"
    regions = {r["name"]: r for r in screen["regions"]}
    assert regions["intel.title"]["action"] == "text"
    assert regions["intel.fight"]["action"] == "exist"
    assert by_id["intel.fight"]["screen_region"] == "intel.fight.view"
    assert by_id["intel.explore"]["screen_region"] == "intel.explore"


def test_analyze_has_no_overlay_rules() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    assert analyze.get("overlay") == []


def test_lighthouse_scenario_taps_fight_marker() -> None:
    scenario = _load_yaml("scenarios/intel_lighthouse.yaml")
    assert scenario["enabled"] is True
    steps = scenario["steps"]
    assert steps[0]["exec"] == "tap_intel_fight"
    assert steps[0]["threshold"] == 0.72
    assert steps[1]["wait_screen"]["any"] == ["intel.fight"]
    assert steps[2] == {"click": "intel.fight.view"}
    assert steps[3]["wait_screen"]["any"] == ["intel.explore"]
    assert steps[4] == {"click": "intel.explore"}
    assert steps[5]["wait_screen"]["any"] == ["squad_settings"]
    assert {"click": "squad_settings.quick_deploy"} in steps
    assert {"click": "squad_settings.fight"} in steps
    assert not any("push_scenario" in str(step) for step in steps)


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
