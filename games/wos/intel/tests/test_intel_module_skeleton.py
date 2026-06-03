"""Structural checks for the intel module skeleton.

Regions and scenarios are added later via the UI labeling flow; this test
only guarantees that the module manifests parse and stay an empty skeleton.
"""
from __future__ import annotations

from pathlib import Path

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


def test_area_is_empty_skeleton() -> None:
    area = _load_yaml("area.yaml")
    assert area.get("version") == 2
    assert area.get("screens") == []


def test_analyze_has_no_overlay_rules() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    assert analyze.get("overlay") == []
