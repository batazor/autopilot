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


def test_learn_skills_is_navigate_only() -> None:
    """After on-device discovery, learn_skills is a NAVIGATE-ONLY surfacer: it
    taps the panel row to open the Expert screen and returns home without auto-
    spending Learning XP. It must never spend (no skill-investment planner yet)."""
    doc = _load_yaml("scenarios/learn_skills.yaml")
    assert doc["enabled"] is True
    assert doc["node"] == "main_menu"
    steps = doc["steps"]
    assert steps[0]["exec"] == "tap_main_menu_panel_row"
    assert steps[0]["section"] == "expert"
    assert steps[0]["row"] == "learn_skills"
    # Navigate-only must NOT spend: no skill-upgrade click/exec anywhere, and no
    # blind system_back (which can pop the Quit dialog on a flaky row_not_found).
    flat = repr(doc).lower()
    assert "upgrade" not in flat
    assert all("system_back" not in s for s in steps)
