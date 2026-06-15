"""Behavioral checks for the chapter module against its reference screenshots.

Replays the two captured frames through the real overlay engine:

* ``references/main.png`` (main_city) — the ``chapter.new`` banner template
  matches and the red counter badge hanging above the cropped bbox is picked
  up via the rule's ``red_dot_bbox`` probe, so the analyze rule that pushes
  ``chapter.claim_missions`` fires.
* ``references/daily.png`` (chapter daily node) — every ``screen_verify`` landmark
  matches, and the regions the claim scenario taps (``chapter.button.claim``,
  ``chapter.button.claim_all``, the ``chapter.close`` X) are all found.
* ``references/growth.png`` (chapter growth node) — the Growth Missions tab
  title is a separate navigation node.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import pytest

from analysis.overlay import load_merged_analyze_yaml
from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation.detector import ScreenDetector
from navigation.screen_graph import route_taps, screen_verify_rules
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
    even when the bot enters the page without the main_city badge having fired."""
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(
        r for r in cfg["overlay"] if r.get("name") == "chapter.claim_all.visible"
    )
    assert set(rule["screens"]) == {"chapter.daily_missions", "chapter.growth_missions"}
    push = rule["steps"][0]["push_scenario"]
    assert (push["name"] if isinstance(push, dict) else push) == "chapter.claim_missions"

    probe = {**rule, "action": "findIcon", "threshold": 0.9}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("daily.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    assert out[rule["name"]]["matched"] is True


def test_claim_overlay_rule_matches_growth_row_button(area_doc: dict) -> None:
    """Per-row Claim buttons are dynamic and should also push the claim pass."""
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(r for r in cfg["overlay"] if r.get("name") == "chapter.claim.visible")
    assert rule["region"] == "chapter.button.claim"
    assert set(rule["screens"]) == {"chapter.daily_missions", "chapter.growth_missions"}
    push = rule["steps"][0]["push_scenario"]
    assert push["name"] == "chapter.claim_missions"

    probe = {**rule}
    probe.pop("screens", None)
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr("growth.png"), area_doc, REPO_ROOT, [probe], state_flat={}
        )
    )
    assert out[rule["name"]]["matched"] is True


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
        for region in ("chapter.button.claim", "chapter.button.claim_all", "chapter.close")
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
    # No generic `chapter` node — the hubs route straight to the daily tab.
    assert route_taps("main_city", "chapter.daily_missions", game="wos") == [
        ["chapter.new"]
    ]
    assert route_taps("main_world", "chapter.daily_missions", game="wos") == [
        ["chapter.new"]
    ]
    assert route_taps(
        "chapter.daily_missions",
        "chapter.growth_missions",
        game="wos",
    ) == [["chapter.growth_missions.title"]]
    assert route_taps(
        "chapter.growth_missions",
        "chapter.daily_missions",
        game="wos",
    ) == [["chapter.daily_missions.title"]]
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
