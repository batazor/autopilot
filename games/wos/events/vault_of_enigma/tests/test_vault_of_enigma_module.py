"""Vault of Enigma: deals sub-tab + main_city icon shortcut."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph, template_icon_resolver  # noqa: F401

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
    """Vault of Enigma is reached via a template-icon resolver from
    ``main_city``, not via a static ``deals → vault`` tap region. From the
    deals hub the BFS therefore backs out to ``main_city`` and then forward
    to the vault — there's no shortcut edge to take.
    """
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        hops = await screen_graph.route_hops_async(
            "deals",
            "deals.vault_of_enigma",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            ("main_city", ["icon.page.back"]),
            (
                "deals.vault_of_enigma",
                [
                    {
                        "region": "main_city.icon_search",
                        "template": "games/wos/events/vault_of_enigma/references/main_city.event.vault_of_enigma.png",
                        "threshold": 0.9,
                        "type": "template_icon",
                    },
                ],
            ),
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()
