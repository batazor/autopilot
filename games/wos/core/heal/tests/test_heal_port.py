"""Structural checks for the heal module ported from the legacy Go bot.

Guards the heal module's structural invariants: every area.yaml region must
have a matching crop tile whose pixel size equals the bbox cut at 720x1280
(the resolution the overlay engine matches against), and the scenario must only
reference regions that exist.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]
AREA = json.loads((MODULE_DIR / "area.yaml").read_text())
W, H = 720, 1280

EXPECTED_SCREENS = {"heal_injured", "heal_injured_replenish", "heal_injured_available"}
EXPECTED_REGIONS = {
    "button.back",
    "heal.button.heal",
    "heal.status",
    "heal.title",
    "heal.button.replenish_all",
    "heal.available",
}


def _bbox_px(b: dict) -> tuple[int, int]:
    left = b["x"] / 100.0 * W
    top = b["y"] / 100.0 * H
    L = max(0, min(int(math.floor(left)), W - 1))
    T = max(0, min(int(math.floor(top)), H - 1))
    R = max(L + 1, min(int(math.ceil(left + b["width"] / 100.0 * W)), W))
    B = max(T + 1, min(int(math.ceil(top + b["height"] / 100.0 * H)), H))
    return R - L, B - T


def test_module_enabled() -> None:
    meta = yaml.safe_load((MODULE_DIR / "module.yaml").read_text())
    assert meta["id"] == "heal"
    assert meta["enabled"] is True


def test_area_schema_and_screens() -> None:
    assert AREA["version"] == 2
    assert {s["screen_id"] for s in AREA["screens"]} == EXPECTED_SCREENS


def test_all_expected_regions_present() -> None:
    names = {r["name"] for s in AREA["screens"] for r in s["regions"]}
    assert names == EXPECTED_REGIONS


def test_every_region_has_a_sized_crop() -> None:
    for screen in AREA["screens"]:
        stem = Path(screen["ocr"]).stem
        for region in screen["regions"]:
            crop = MODULE_DIR / "references" / "crop" / f"{stem}_{region['name']}.png"
            assert crop.is_file(), f"missing crop {crop.name}"
            img = cv2.imread(str(crop))
            assert img is not None, f"unreadable crop {crop.name}"
            exp_w, exp_h = _bbox_px(region["bbox"])
            ph, pw = img.shape[:2]
            assert (pw, ph) == (exp_w, exp_h), (
                f"{crop.name}: crop {pw}x{ph} != bbox@720x1280 {exp_w}x{exp_h}"
            )


def test_reference_screenshots_are_target_resolution() -> None:
    for screen in AREA["screens"]:
        ref = MODULE_DIR / screen["ocr"]
        img = cv2.imread(str(ref))
        assert img is not None, f"missing reference {ref}"
        assert img.shape[:2] == (H, W), f"{ref.name} is {img.shape[1]}x{img.shape[0]}, want {W}x{H}"


def test_scenario_only_references_known_regions() -> None:
    scenario = (MODULE_DIR / "scenarios" / "heal_injured.yaml").read_text()
    doc = yaml.safe_load(scenario)
    used: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key in ("click", "long_click", "match", "while_match"):
                val = node.get(key)
                if isinstance(val, str):
                    used.add(val)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    assert used <= EXPECTED_REGIONS, f"scenario references unknown regions: {used - EXPECTED_REGIONS}"
