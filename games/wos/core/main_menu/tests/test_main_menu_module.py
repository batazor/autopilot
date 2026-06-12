"""Structural checks for the main_menu navigation node."""
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


def test_module_manifest_declares_main_menu() -> None:
    manifest = _load_yaml("module.yaml")
    assert manifest["id"] == "main_menu"
    assert manifest["title"] == "Main menu"


def test_edge_taps_enter_and_leave_main_menu() -> None:
    edges = _load_yaml("routes/edge_taps.yaml")["edges"]
    assert edges["main_city"]["main_menu"] == ["main_city.to.main_menu"]
    assert edges["main_menu"]["main_city"] == ["icon.page.back"]


def test_screen_graph_exposes_main_menu_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_edge_taps_cache()
    screen_graph.invalidate_screen_verify_config()

    static, _dynamic, _graph = screen_graph.graph_for_game("wos")
    assert static[("main_city", "main_menu")] == ["main_city.to.main_menu"]
    assert static[("main_menu", "main_city")] == ["icon.page.back"]
    assert screen_graph.route_taps("main_city", "main_menu") == [
        ["main_city.to.main_menu"]
    ]
    assert screen_graph.screen_verify_rules("main_menu") == [
        {"from_screen": ["main_city"]}
    ]
