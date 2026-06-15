"""1st Purchase icon aliases to the canonical 7-Day event."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from layout.area_manifest import load_area_doc
from navigation import screen_graph, template_icon_resolver  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
TEMPLATE = (
    "games/wos/events/first_purchase/references/main_city.event.first_purchase.png"
)


def _load_main_city_icon_search() -> tuple[object, dict]:
    trials_dir = REPO_ROOT / "games/wos/events/trials/references"
    frame_path = trials_dir / "main_city.trials.png"
    frame = cv2.imread(str(frame_path))
    assert frame is not None, f"missing fixture: {frame_path}"
    return frame, load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_main_city_first_purchase_icon_pushes_7_day() -> None:
    frame, area_doc = _load_main_city_icon_search()
    doc = load_analyze_yaml(MODULE_DIR / "analyze/analyze.yaml")
    rule = doc["overlay"][0]
    assert rule["steps"] == [{"push_scenario": {"name": "event.7-day", "ttl": "1m"}}]

    # The production rule is red-dot gated; this fixture only proves the
    # legacy 1st Purchase icon template still resolves on the main-city strip.
    match_rule = {
        key: value
        for key, value in rule.items()
        if key not in {"isRedDot", "steps"}
    }
    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [match_rule],
        current_screen="main_city",
    )
    hit = out[match_rule["name"]]
    assert hit.get("matched") is True, hit


def test_event_first_purchase_screen_is_not_registered() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        assert screen_graph.screen_verify_rules("event.first_purchase") == []
        assert screen_graph.screen_landmark_rules("event.first_purchase") == []
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_main_city_does_not_route_to_event_first_purchase() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        hops = await screen_graph.route_hops_async(
            "main_city",
            "event.first_purchase",
            instance_id="bs1",
            redis_client=None,
        )
        assert hops is None
    finally:
        screen_graph.load_screen_verify_config.cache_clear()
