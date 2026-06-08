"""Structural checks for the chat module navigation wiring.

Guarantees that the manifests parse, the World / Alliance / Personal tab
regions are labeled, screen detection tells the tabs apart via active-tab
detection, and every chat screen can route between tabs and back to main_city.
"""
from __future__ import annotations

from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]

TABS = ("world", "alliance", "personal")


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_module_manifest_declares_chat() -> None:
    manifest = _load_yaml("module.yaml")
    assert manifest["id"] == "chat"
    assert manifest["title"] == "Chat"


def test_area_declares_title_and_tab_regions() -> None:
    area = _load_yaml("area.yaml")
    assert area.get("version") == 2
    names = {r["name"] for s in area.get("screens", []) for r in s.get("regions", [])}
    assert "chat.title" in names
    for tab in TABS:
        assert f"chat.{tab}" in names


def test_screen_verify_detects_tabs_via_tab_active() -> None:
    screens = _load_yaml("routes/screen_verify.yaml")["screens"]

    # Parent screen: just the title landmark.
    assert screens["chat"]["rules"][0]["match"] == "chat.title"

    for tab in TABS:
        node = f"chat.{tab}"
        assert screens[node]["parent"] == "chat"
        rule = screens[node]["rules"][0]
        assert rule["match"] == "chat.title"
        # Active-tab is read off the tab button region itself.
        assert rule["tab_active"] == node


def test_edge_taps_switch_tabs_and_back_to_main_city() -> None:
    edges = _load_yaml("routes/edge_taps.yaml")["edges"]

    # Entry: both hubs route into chat via the same shortcut button.
    assert edges["main_city"]["chat"] == ["main_city.to.chat"]
    assert edges["main_world"]["chat"] == ["main_city.to.chat"]

    tab_nodes = ("chat", *(f"chat.{t}" for t in TABS))
    for node in tab_nodes:
        # Every chat screen returns to main_city via the shared back button.
        assert edges[node]["main_city"] == ["icon.page.back"], (
            f"{node} must return to main_city via icon.page.back"
        )

    # Switching to a tab taps that tab's own button.
    for from_node in tab_nodes:
        for tab in TABS:
            target = f"chat.{tab}"
            if target == from_node:
                continue
            assert edges[from_node][target] == [target]
