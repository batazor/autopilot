"""Scaffold checks for the island node + disabled Life Essence claim."""
from __future__ import annotations

from pathlib import Path

import yaml

from navigation import screen_graph
from services import bind_active_game

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_module_manifest() -> None:
    assert _load_yaml("module.yaml")["id"] == "island"


def test_claim_ships_disabled() -> None:
    doc = _load_yaml("scenarios/claim_life_essence.yaml")
    assert doc["enabled"] is False
    assert doc["steps"][0]["exec"] == "tap_main_menu_panel_row"


def test_screen_graph_exposes_island_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_screen_verify_config()
    rules = screen_graph.screen_verify_rules("island")
    assert rules, "island must be a verifiable FSM node"
    assert any(r.get("ocr") == "island.title" for r in rules)
