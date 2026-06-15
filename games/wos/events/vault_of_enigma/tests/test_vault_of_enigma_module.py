"""Vault of Enigma: deals sub-tab + main_city icon shortcut."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph, tab_index_resolver, template_icon_resolver  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE = (
    "games/wos/events/vault_of_enigma/references/main_city.event.vault_of_enigma.png"
)


def _load_main_city_frame():
    trials_dir = REPO_ROOT / "games/wos/events/trials/references"
    frame_path = trials_dir / "main_city.trials.png"
    frame = cv2.imread(str(frame_path))
    assert frame is not None, f"missing fixture: {frame_path}"
    return frame, load_area_doc(REPO_ROOT)


def _load_yaml(rel: str) -> dict:
    return yaml.safe_load((MODULE_DIR / rel).read_text(encoding="utf-8")) or {}


def _load_reference_frame(name: str):
    path = MODULE_DIR / "references" / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"missing fixture: {path}"
    return frame


@pytest.mark.asyncio
async def test_main_city_vault_icon_template_loads() -> None:
    """Template PNG exists and overlay rule runs (match may fail on trials fixture)."""
    frame, area_doc = _load_main_city_frame()
    rule = {
        "name": "vault_of_enigma.main_city.event_icon.visible",
        "region": "main_city.icon_search",
        "action": "findIcon",
        "template": TEMPLATE,
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )
    assert rule["name"] in out


def test_screen_verify_registers_deals_vault_of_enigma() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        # ``routes/screen_verify.yaml`` pins this at 0.74 — the icon is small
        # and overprinted with the event ribbon, so the stricter 0.9 from the
        # underlying ``area.yaml`` region was missing the right frame.
        expected = [{"match": "vault_of_enigma.title", "threshold": 0.74}]
        assert screen_graph.screen_verify_rules("deals.vault_of_enigma") == expected
        assert screen_graph.screen_landmark_rules("deals.vault_of_enigma") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_reference_detects_vault_title_and_box_red_dot() -> None:
    frame = _load_reference_frame("page.vault_of_enigma.png")
    area_doc = load_area_doc(REPO_ROOT)
    rules = [
        {
            "name": "title",
            "region": "vault_of_enigma.title",
            "action": "findIcon",
            "threshold": 0.74,
        },
        {
            "name": "box_red_dot",
            "region": "vault_of_enigma.box",
            "isRedDot": True,
            "threshold": 0.85,
        },
    ]
    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        rules,
        current_screen="deals.vault_of_enigma",
    )

    assert out["title"]["matched"] is True
    assert out["box_red_dot"]["matched"] is True
    assert out["box_red_dot"]["red_dot_present"] is True


def test_box_red_dot_analyzer_pushes_vault_scenario() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = analyze["overlay"]
    box_rule = next(r for r in rules if r["name"] == "vault_of_enigma.box.has_red_dot")

    assert box_rule["region"] == "vault_of_enigma.box"
    assert box_rule["isRedDot"] is True
    assert "deals.vault_of_enigma" in box_rule["screens"]
    assert box_rule["steps"] == [
        {"push_scenario": {"name": "deals.vault_of_enigma", "ttl": "1m"}}
    ]


def test_vault_scenario_clicks_box_when_red_dot_is_present() -> None:
    scenario = _load_yaml("scenarios/deals.vault_of_enigma.yaml")
    first_step = scenario["steps"][0]

    assert scenario["node"] == "deals.vault_of_enigma"
    assert first_step["while_match"] == "vault_of_enigma.box"
    assert first_step["isRedDot"] is True
    assert first_step["steps"][0] == {"click": "vault_of_enigma.box"}


@pytest.mark.asyncio
async def test_main_city_routes_to_deals_vault_of_enigma() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        hops = await screen_graph.route_hops_async(
            "main_city",
            "deals.vault_of_enigma",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (
                "deals.vault_of_enigma",
                [
                    {
                        "type": "template_icon",
                        "region": "main_city.icon_search",
                        "template": TEMPLATE,
                        "threshold": 0.9,
                    }
                ],
            )
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_deals_hub_routes_to_vault_tab() -> None:
    """From the Deals hub, Vault is a direct top-tab click."""
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        hops = await screen_graph.route_hops_async(
            "deals",
            "deals.vault_of_enigma",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (
                "deals.vault_of_enigma",
                [
                    {
                        "type": "tab_index",
                        "region": "deals.tabs_strip",
                        "index": 0,
                    },
                ],
            ),
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()
