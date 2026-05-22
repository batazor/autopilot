"""1st Purchase event: main_city icon + screen_verify registration."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
TEMPLATE = (
    "modules/events/first_purchase/references/main_city.event.first_purchase.png"
)


def _load_main_city_icon_search() -> tuple[object, dict]:
    trials_dir = REPO_ROOT / "modules/events/trials/references"
    frame_path = trials_dir / "main_city.trials.png"
    frame = cv2.imread(str(frame_path))
    assert frame is not None, f"missing fixture: {frame_path}"
    return frame, load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_main_city_first_purchase_icon_visible() -> None:
    frame, area_doc = _load_main_city_icon_search()
    rule = {
        "name": "first_purchase.main_city.event_icon.visible",
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
    hit = out[rule["name"]]
    assert hit.get("matched") is True, hit


def test_screen_verify_registers_event_first_purchase() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        expected = [{"match": "event.first_purchase", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("event.first_purchase") == expected
        assert screen_graph.screen_landmark_rules("event.first_purchase") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_main_city_routes_to_event_first_purchase() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        hops = await screen_graph.route_hops_async(
            "main_city",
            "event.first_purchase",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops == [
            (
                "event.first_purchase",
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
