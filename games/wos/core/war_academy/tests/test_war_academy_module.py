"""Scaffold checks for the war_academy node + disabled idle dispatcher."""
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
    assert _load_yaml("module.yaml")["id"] == "war_academy"


def test_dispatcher_ships_disabled() -> None:
    """The panel dispatch references start_idle_war_academy; it must ship disabled
    (self-gate keeps it dormant) until the War Academy tech-tree is labeled."""
    doc = _load_yaml("scenarios/start_idle_war_academy.yaml")
    assert doc["enabled"] is False
    assert doc["steps"][0]["exec"] == "dismiss_popup"


def test_screen_graph_exposes_war_academy_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_screen_verify_config()
    rules = screen_graph.screen_verify_rules("war_academy")
    assert rules, "war_academy must be a verifiable FSM node"
    assert any(r.get("ocr") == "war_academy.title" for r in rules)
