"""Structural invariants for modules ported from the legacy Go bot.

Guards the structural invariants every ported module must satisfy:

* every area.yaml region has a crop tile (except `text`/`color_check`, which the
  engine evaluates without a template) sized exactly to the bbox cut at 720x1280,
* reference screenshots are 720x1280,
* every scenario only references regions that exist in the module's area.yaml,
* the module is enabled.

Add a module's path to ``PORTED_MODULES`` when porting it.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import cv2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
W, H = 720, 1280

PORTED_MODULES = (
    "games/wos/core/heal",
    "games/wos/core/chapter",
    "games/wos/alliance/chest",
    "games/wos/core/chief_orders",
)

# Actions whose detection uses the crop as a 1:1 template (size must match bbox).
TEMPLATE_ACTIONS = {"exist", "findIcon"}


def _bbox_px(b: dict) -> tuple[int, int]:
    left = b["x"] / 100.0 * W
    top = b["y"] / 100.0 * H
    L = max(0, min(int(math.floor(left)), W - 1))
    T = max(0, min(int(math.floor(top)), H - 1))
    R = max(L + 1, min(int(math.ceil(left + b["width"] / 100.0 * W)), W))
    B = max(T + 1, min(int(math.ceil(top + b["height"] / 100.0 * H)), H))
    return R - L, B - T


def _area(module: str) -> dict:
    return json.loads((REPO_ROOT / module / "area.yaml").read_text())


def _regions_used_in_scenario(doc: object, out: set[str]) -> None:
    if isinstance(doc, dict):
        for key in ("click", "long_click", "match", "while_match"):
            val = doc.get(key)
            if isinstance(val, str):
                out.add(val)
        for v in doc.values():
            _regions_used_in_scenario(v, out)
    elif isinstance(doc, list):
        for item in doc:
            _regions_used_in_scenario(item, out)


def _region_names(module: str) -> set[str]:
    return {r["name"] for s in _area(module)["screens"] for r in s["regions"]}


@functools.lru_cache(maxsize=1)
def _all_region_names() -> set[str]:
    """Every region across all modules' merged area docs.

    Region names are globally unique, and a scenario may legitimately tap a
    region owned by another module (e.g. the shared ``icon.page.back`` button),
    so scenarios are validated against the global set — same source of truth as
    ``config.startup_validation`` — rather than only their own module's regions.
    """
    from layout.area_manifest import load_area_doc

    doc = load_area_doc(REPO_ROOT)
    return {r["name"] for s in doc.get("screens") or [] for r in s.get("regions") or []}


@pytest.mark.parametrize("module", PORTED_MODULES)
def test_module_enabled(module: str) -> None:
    meta = yaml.safe_load((REPO_ROOT / module / "module.yaml").read_text())
    assert meta.get("enabled") is True, f"{module} not enabled"


@pytest.mark.parametrize("module", PORTED_MODULES)
def test_area_is_v2_with_screens(module: str) -> None:
    area = _area(module)
    assert area["version"] == 2
    assert area["screens"], f"{module} has no screens"


@pytest.mark.parametrize("module", PORTED_MODULES)
def test_template_crops_match_bbox_size(module: str) -> None:
    mod_dir = REPO_ROOT / module
    for screen in _area(module)["screens"]:
        stem = Path(screen["ocr"]).stem
        for region in screen["regions"]:
            if region["action"] not in TEMPLATE_ACTIONS:
                continue
            crop = mod_dir / "references" / "crop" / f"{stem}_{region['name']}.png"
            assert crop.is_file(), f"missing crop {crop.relative_to(REPO_ROOT)}"
            img = cv2.imread(str(crop))
            assert img is not None, f"unreadable crop {crop.name}"
            exp_w, exp_h = _bbox_px(region["bbox"])
            ph, pw = img.shape[:2]
            assert (pw, ph) == (exp_w, exp_h), (
                f"{module}:{crop.name} crop {pw}x{ph} != bbox@720x1280 {exp_w}x{exp_h}"
            )


@pytest.mark.parametrize("module", PORTED_MODULES)
def test_reference_screenshots_are_target_resolution(module: str) -> None:
    mod_dir = REPO_ROOT / module
    for screen in _area(module)["screens"]:
        ref = mod_dir / screen["ocr"]
        img = cv2.imread(str(ref))
        assert img is not None, f"missing reference {ref}"
        assert img.shape[:2] == (H, W), f"{ref.name} is {img.shape[1]}x{img.shape[0]}"


@pytest.mark.parametrize("module", PORTED_MODULES)
def test_scenarios_reference_known_regions(module: str) -> None:
    known = _all_region_names()
    scen_dir = REPO_ROOT / module / "scenarios"
    for yml in scen_dir.glob("*.yaml"):
        doc = yaml.safe_load(yml.read_text())
        used: set[str] = set()
        _regions_used_in_scenario(doc, used)
        assert used <= known, f"{module}:{yml.name} unknown regions: {used - known}"
