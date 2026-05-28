"""Structural checks for the chat module skeleton.

Regions and scenarios are added later via the UI labeling flow; this test
only guarantees that the module manifests parse and the navigation graph
declares the world / alliance / personal tabs with a back edge to main_city.
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


def test_module_manifest_declares_chat() -> None:
    manifest = _load_yaml("module.yaml")
    assert manifest["id"] == "chat"
    assert manifest["title"] == "Chat"


def test_area_is_empty_skeleton() -> None:
    area = _load_yaml("area.yaml")
    assert area.get("version") == 2
    assert area.get("screens") == []


def test_edge_taps_cover_tabs_and_back_to_main_city() -> None:
    edges = _load_yaml("routes/edge_taps.yaml")["edges"]

    assert edges["main_city"]["chat"] == ["page.chat"]

    tab_nodes = ("chat", "chat.world", "chat.alliance", "chat.personal")
    for node in tab_nodes:
        assert edges[node]["main_city"] == ["icon.page.back"], (
            f"{node} must return to main_city via icon.page.back"
        )

    for from_node in tab_nodes:
        for tab in ("world", "alliance", "personal"):
            target = f"chat.{tab}"
            if target == from_node:
                continue
            assert edges[from_node][target] == [f"chat.tab.{tab}"]


def test_screen_verify_uses_tab_active_detection() -> None:
    screens = _load_yaml("routes/screen_verify.yaml")["screens"]

    assert "chat" in screens
    for tab in ("world", "alliance", "personal"):
        node = f"chat.{tab}"
        assert screens[node]["parent"] == "chat"
        rule = screens[node]["rules"][0]
        assert rule["match"] == "chat.title"
        assert rule["tab_active"] == f"chat.tab.{tab}"
