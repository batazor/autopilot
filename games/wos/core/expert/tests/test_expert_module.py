"""Scaffold checks for the expert module + disabled learn_skills (UX TBD)."""
from __future__ import annotations

from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_module_manifest() -> None:
    assert _load_yaml("module.yaml")["id"] == "expert"


def test_learn_skills_ships_disabled() -> None:
    """Expert UX is unknown → learn_skills must stay disabled until discovery."""
    doc = _load_yaml("scenarios/learn_skills.yaml")
    assert doc["enabled"] is False
    assert doc["steps"][0]["exec"] == "tap_main_menu_panel_row"
