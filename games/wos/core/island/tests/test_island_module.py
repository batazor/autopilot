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


def test_claim_life_essence_is_wired() -> None:
    """After on-device labeling the Life Essence claim is live: it taps the panel
    row, waits for the island node, then taps the collect bubble and returns."""
    doc = _load_yaml("scenarios/claim_life_essence.yaml")
    assert doc["enabled"] is True
    assert doc["node"] == "main_menu"
    steps = doc["steps"]
    assert steps[0]["exec"] == "tap_main_menu_panel_row"
    assert steps[0]["section"] == "life_essence"
    assert steps[0]["row"] == "tree_of_life"
    # The collect + return live under the island-node guard.
    guarded = next(s for s in steps if s.get("cond") == "currentNode == island")
    inner = guarded["steps"]
    assert any(s.get("click") == "island.life_essence.collect" for s in inner)
    assert any(s.get("click") == "island.to_city" for s in inner)


def test_island_area_has_collect_and_return_targets() -> None:
    doc = _load_yaml("area.yaml")
    regions = {r["name"] for sc in doc["screens"] for r in sc.get("regions", [])}
    assert {"island.title", "island.life_essence.collect", "island.to_city"} <= regions


def test_screen_graph_exposes_island_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_screen_verify_config()
    rules = screen_graph.screen_verify_rules("island")
    assert rules, "island must be a verifiable FSM node"
    assert any(r.get("ocr") == "island.title" for r in rules)
