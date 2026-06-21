"""Behavioral checks for the chapter module against its reference screenshots.

Replays the two captured frames through the real overlay engine:

* ``references/main.png`` (main_city) — the ``chapter.new`` banner template
  matches and the red counter badge hanging above the cropped bbox is picked
  up via the rule's ``red_dot_bbox`` probe, so the analyze rule that pushes
  ``chapter.claim_missions`` fires.
* ``references/daily.png`` (chapter daily node) — every ``screen_verify`` landmark
  matches, Claim All / close template taps are present, and row-level Claim
  buttons are found through the green-button mask.
* ``references/growth.png`` (chapter growth node) — the Growth Missions tab
  title is a separate navigation node.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay import load_merged_analyze_yaml, run_overlay_analysis
from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.green_button_detector import find_green_buttons
from navigation.detector import ScreenDetector
from navigation.screen_graph import graph_for_game, route_taps, screen_verify_rules
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT, game="wos")


def _load_reference_bgr(name: str):
    frame = cv2.imread(str(MODULE_DIR / "references" / name))
    assert frame is not None, f"failed to load reference screenshot: {name}"
    return frame


def test_badge_overlay_rule_matches_with_red_dot(area_doc: dict) -> None:
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.badge.visible"
    )
    assert rule["screens"] == ["main_city", "main_world"]
    assert rule["steps"] == [{"push_scenario": "chapter.claim_missions"}]

    probe = {**rule, "action": "findIcon", "threshold": 0.9}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("main.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    row = out[rule["name"]]
    assert row["matched"] is True
    assert row["red_dot_present"] is True


def test_claim_all_overlay_rule_matches_on_page(area_doc: dict) -> None:
    """On-page rule: a lit Claim All on the chapter panel pushes the claim pass
    even when the bot enters the page without the main_city badge having fired.
    Each tab is its own node, so its rule pushes the node-bound scenario."""
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    daily = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.claim_all.visible.daily"
    )
    growth = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.claim_all.visible.growth"
    )
    assert daily["screens"] == ["chapter.daily_missions"]
    assert growth["screens"] == ["chapter.growth_missions"]
    assert daily["steps"][0]["push_scenario"]["name"] == "chapter.claim_missions"
    assert growth["steps"][0]["push_scenario"]["name"] == "chapter.growth.claim"

    probe = {**daily, "action": "findIcon", "threshold": 0.9}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("daily.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    assert out[daily["name"]]["matched"] is True


def test_claim_overlay_rule_matches_growth_row_button(area_doc: dict) -> None:
    """Per-row Claim buttons are dynamic and should also push the claim pass.
    On the Growth tab the rule pushes the Growth-node scenario."""
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.claim.visible.growth"
    )
    assert rule["region"] == "chapter.button.claim"
    assert rule["action"] == "cta_button"
    assert rule["color"] == "green"
    assert rule["threshold"] == 0.5
    assert rule["screens"] == ["chapter.growth_missions"]
    assert rule["steps"][0]["push_scenario"]["name"] == "chapter.growth.claim"

    probe = {**rule}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("growth.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    assert out[rule["name"]]["matched"] is True
    assert out[rule["name"]]["action"] == "cta_button"
    assert out[rule["name"]]["detector_action"] == "green_button"
    assert out[rule["name"]]["top_left"] == [518, 545]


def test_green_button_mask_finds_all_daily_chapter_ctas() -> None:
    hits = find_green_buttons(_load_reference_bgr("daily.png"), min_score=0.5)
    assert len(hits) == 3
    assert [(h.top_left, h.width, h.height) for h in hits] == [
        ((236, 994), 248, 73),
        ((519, 545), 155, 65),
        ((519, 712), 155, 65),
    ]


def test_green_button_overlay_rule_respects_exclusions_for_while_match(
    area_doc: dict,
) -> None:
    rule = {
        "name": "chapter.claim.visible.test",
        "region": "chapter.button.claim",
        "action": "cta_button",
        "color": "green",
        "threshold": 0.5,
    }
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("daily.png"),
            area_doc,
            REPO_ROOT,
            [rule],
            state_flat={},
        )
    )
    first = out[rule["name"]]
    assert first["matched"] is True
    assert first["action"] == "cta_button"
    assert first["detector_action"] == "green_button"
    assert first["top_left"] == [519, 545]

    second_rule = {
        **rule,
        "exclude_top_lefts": [first["top_left"]],
        "exclude_radius_px": 24,
    }
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("daily.png"),
            area_doc,
            REPO_ROOT,
            [second_rule],
            state_flat={},
        )
    )
    second = out[rule["name"]]
    assert second["matched"] is True
    assert second["top_left"] == [519, 712]


def test_generic_chapter_claim_rule_clicks_visible_claim(area_doc: dict) -> None:
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.claim.visible.generic"
    )
    assert rule["region"] == "chapter.button.claim"
    assert rule["action"] == "cta_button"
    assert rule["color"] == "green"
    assert rule["threshold"] == 0.5
    assert rule["screens"] == ["chapter"]
    assert rule["device_level"] is True
    assert rule["steps"] == [{"click": "chapter.button.claim"}]

    probe = {**rule}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("chapter.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    row = out[rule["name"]]
    assert row["matched"] is True
    assert row["action"] == "cta_button"
    assert row["detector_action"] == "green_button"
    assert row["score"] == pytest.approx(0.751953125)
    assert row["fill_ratio"] == pytest.approx(0.839145, abs=0.0001)
    assert row["candidate_count"] == 1
    assert row["excluded_count"] == 0
    assert row["min_fill_ratio"] == 0.45
    assert row["top_left"] == [522, 977]
    assert row["template_w"] == 148
    assert row["template_h"] == 62

    device_level_out = asyncio.run(
        run_overlay_analysis(
            _load_reference_bgr("chapter.png"),
            repo_root=REPO_ROOT,
            area_doc=area_doc,
            current_screen="chapter",
            device_level_only=True,
            module_scope="chapter",
        )
    )
    assert device_level_out[rule["name"]]["matched"] is True


def test_claim_scenarios_use_green_button_mask_for_row_claims() -> None:
    for rel in (
        "scenarios/chapter.claim_missions.yaml",
        "scenarios/chapter.growth.claim.yaml",
        "scenarios/chapter.claim.visible.yaml",
    ):
        doc = yaml.safe_load((MODULE_DIR / rel).read_text())
        claim_steps = [
            step for step in doc["steps"] if step.get("while_match") == "chapter.button.claim"
        ]
        assert claim_steps, rel
        assert claim_steps[0]["action"] == "cta_button"
        assert claim_steps[0]["color"] == "green"
        assert claim_steps[0]["threshold"] == 0.5
        assert claim_steps[0]["max"] == 6
        assert "template" not in claim_steps[0]


def _assert_screen_verify_matches_reference(
    screen: str,
    reference: str,
    area_doc: dict,
) -> None:
    rules = screen_verify_rules(screen)
    assert rules, f"{screen} screen_verify rules missing"
    probes = [
        {
            "name": f"verify.{r['match']}",
            "action": "findIcon",
            "region": r["match"],
            "threshold": r.get("threshold", 0.9),
        }
        for r in rules
    ]
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr(reference), area_doc, REPO_ROOT, probes, state_flat={}
        )
    )
    for probe in probes:
        assert out[probe["name"]]["matched"] is True, probe["region"]


def test_screen_verify_landmarks_match_daily_reference(area_doc: dict) -> None:
    _assert_screen_verify_matches_reference("chapter.daily_missions", "daily.png", area_doc)


def test_screen_verify_landmarks_match_growth_reference(area_doc: dict) -> None:
    _assert_screen_verify_matches_reference(
        "chapter.growth_missions",
        "growth.png",
        area_doc,
    )


def test_screen_verify_landmarks_match_generic_chapter_reference(area_doc: dict) -> None:
    _assert_screen_verify_matches_reference("chapter", "chapter.png", area_doc)


@pytest.mark.asyncio
async def test_chapter_tab_references_detect_specific_nodes() -> None:
    detector = ScreenDetector(get_ocr_client())

    assert await detector.detect_screen(_load_reference_bgr("daily.png")) == (
        "chapter.daily_missions"
    )
    assert await detector.detect_screen(_load_reference_bgr("growth.png")) == (
        "chapter.growth_missions"
    )


def test_claim_scenario_tap_targets_match_daily_reference(area_doc: dict) -> None:
    probes = [
        {"name": f"p.{region}", "action": "findIcon", "region": region, "threshold": 0.9}
        for region in ("chapter.button.claim_all", "chapter.close")
    ]
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("daily.png"), area_doc, REPO_ROOT, probes, state_flat={}
        )
    )
    for probe in probes:
        assert out[probe["name"]]["matched"] is True, probe["region"]


@pytest.mark.asyncio
async def test_refreshes_in_ocr_reads_reset_clock(area_doc: dict) -> None:
    """The daily reset clock must OCR as ``hh:mm:ss`` — it feeds the
    ``chapter.daily.refresh`` event timer (push expiries + next-day review)."""
    import re

    from layout.types import Region
    from services import get_ocr_client

    region = next(
        r
        for s in area_doc["screens"]
        for r in s.get("regions") or []
        if r.get("name") == "chapter.daily.refreshes_in"
    )
    b = region["bbox"]
    frame = _load_reference_bgr("daily.png")
    h, w = frame.shape[:2]
    res = await get_ocr_client().ocr_region(
        frame,
        Region(
            int(b["x"] / 100 * w),
            int(b["y"] / 100 * h),
            int(b["width"] / 100 * w),
            int(b["height"] / 100 * h),
        ),
    )
    assert re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", res.text.strip()), res.text


def test_chapter_node_routes_from_both_hubs() -> None:
    # Hubs route straight to the daily tab; generic `chapter` is only a fallback
    # detected from the common title when the active tab landmark misses.
    assert route_taps("main_city", "chapter.daily_missions", game="wos") == [
        ["chapter.new"]
    ]
    assert route_taps("main_world", "chapter.daily_missions", game="wos") == [
        ["chapter.new"]
    ]
    # Tab ↔ tab switching is a dynamic tab_index edge over the segmented strip
    # (Growth = index 0, Daily = index 1) — it can't be resolved statically, so
    # route_taps returns None; assert the resolver spec on the dynamic graph.
    _static, dynamic, _graph = graph_for_game("wos")
    assert dynamic[("chapter.daily_missions", "chapter.growth_missions")] == {
        "resolver": "tab_index",
        "region": "chapter.tabs_strip",
        "index": 0,
    }
    assert dynamic[("chapter.growth_missions", "chapter.daily_missions")] == {
        "resolver": "tab_index",
        "region": "chapter.tabs_strip",
        "index": 1,
    }
    assert dynamic[("chapter", "chapter.daily_missions")] == {
        "resolver": "tab_index",
        "region": "chapter.tabs_strip",
        "index": 1,
    }
    assert dynamic[("chapter", "chapter.growth_missions")] == {
        "resolver": "tab_index",
        "region": "chapter.tabs_strip",
        "index": 0,
    }
    assert (
        route_taps("chapter.daily_missions", "chapter.growth_missions", game="wos")
        is None
    )
    assert route_taps("chapter", "chapter.daily_missions", game="wos") is None
    assert route_taps("chapter", "main_city", game="wos") == [["chapter.close"]]
    assert route_taps("chapter.daily_missions", "main_city", game="wos") == [
        ["chapter.close"]
    ]
    assert route_taps("chapter.growth_missions", "main_city", game="wos") == [
        ["chapter.close"]
    ]


def test_daily_review_cron_spec_discovered() -> None:
    """The 12h cron trampoline must be picked up by the scheduler's cron
    discovery and push the claim pass at above-average priority (the bare
    push_scenario inherits the trampoline's own priority)."""
    import yaml

    from dsl.cron_specs import iter_cron_yaml_files_for_repo

    path = next(
        p
        for p in iter_cron_yaml_files_for_repo(REPO_ROOT)
        if p.name == "chapter.daily.review.cron.yaml"
    )
    raw = yaml.safe_load(path.read_text())
    assert raw["enabled"] is True
    assert raw["cron"] == "0 */12 * * *"
    assert raw["priority"] == 90_000
    assert raw["steps"] == [{"push_scenario": "chapter.claim_missions"}]
