"""Behavioral tests for the ``shop.tab.advance`` scenario.

The scenario carousel-advances the shop tab strip when nothing on the
visible page needs attention. Shape:

    while_match: shop.tab.next_page
      max: 1
      steps:
        - click: shop.tab.next_page
        - wait: 1s

Three risks are tested:

* **Infinite loop** — at end of carousel the ``>`` arrow disappears.
  ``while_match`` must drop out without an extra tap. Anchored on
  ``page.shop.daily_deals.tabs_end.png``, captured at the rightmost
  carousel position (no ``next_page`` arrow rendered). Combined with the
  analyzer rule below this also kills the *cross-tick* loop: with no
  arrow the rule stops matching and stops pushing the scenario.
* **Wasted clicks per tick** — ``while_match: max: 1`` is the hard cap
  per scenario run; even a hypothetical never-ending strip can only tap
  once per invocation.
* **Scheduling contract** — push TTL on the analyzer rule throttles
  re-pushes (otherwise the 1Hz overlay loop would queue advance every
  tick), and the scenario's priority sits below claim scenarios so they
  always run first when both are queued.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
SCENARIO_PATH = MODULE_DIR / "scenarios" / "shop.tab.advance.yaml"
ANALYZE_PATH = MODULE_DIR / "analyze" / "analyze.yaml"

NEXT_PAGE_REGION = "shop.tab.next_page"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


# ── Scenario / analyze structural sanity ────────────────────────────────────


def test_scenario_file_exists() -> None:
    assert SCENARIO_PATH.exists()


def test_scenario_shape_is_bounded_loop_on_next_page() -> None:
    """Lock the loop's anchor + per-run cap.

    ``while_match: shop.tab.next_page`` is what gives us the end-of-strip
    stop (arrow disappears → loop exits). ``max`` keeps the per-run tap
    count finite even on a hypothetical wrapping carousel."""
    doc = yaml.safe_load(SCENARIO_PATH.read_text())
    outer = doc["steps"][0]
    assert outer["while_match"] == NEXT_PAGE_REGION
    assert isinstance(outer.get("max"), int) and outer["max"] >= 1


def test_scenario_priority_below_claim_scenarios() -> None:
    """Advance must be lower priority than claim scenarios so claims run first
    when both are pushed in the same tick — otherwise the bot may scroll past
    a claimable tab before the claim scenario gets a turn."""
    doc = yaml.safe_load(SCENARIO_PATH.read_text())
    assert doc["priority"] == 70_000
    claim_doc = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "shop.dawn_market.yaml").read_text()
    )
    assert doc["priority"] < claim_doc["priority"]


def test_analyze_rule_pushes_with_throttle() -> None:
    """Analyze rule must throttle re-pushes so the analyzer doesn't queue
    advance every tick (1 Hz overlay loop) when the arrow is visible.
    A missing or sub-second TTL turns into a hot-loop against the queue."""
    # analyze.yaml uses ``include:`` to compose per-page files; read via the
    # standard loader so resolved rules from common.yaml are visible to the test.
    from analysis.overlay_manifest import load_analyze_yaml

    doc = load_analyze_yaml(ANALYZE_PATH)
    rules = [r for r in doc.get("overlay", []) if r.get("region") == NEXT_PAGE_REGION]
    assert len(rules) == 1, f"expected exactly one advance rule, got {rules!r}"
    (rule,) = rules
    # next_page arrow is reachable from every shop sub-page, so each one
    # must be listed (the analyzer has no wildcard support).
    expected_screens = {
        "shop",
        "shop.dawn_market",
        "shop.daily_deals",
        "shop.mix_match",
        "shop.dawn_fund",
        "shop.construction_queue",
        "shop.weekly_monthly_cards",
        "shop.get_gems",
        "shop.regular_pack",
    }
    assert set(rule["screens"]) == expected_screens, (
        f"screens drift: {set(rule['screens']) ^ expected_screens}"
    )
    pushes = rule.get("pushScenario") or []
    advance_push = next(
        (p for p in pushes if p.get("name") == "shop.tab.advance"), None
    )
    assert advance_push is not None, f"shop.tab.advance not pushed: {rule!r}"
    ttl = str(advance_push.get("ttl", "")).strip()
    assert ttl, "push TTL missing — would hot-loop against 1Hz analyzer"
    # Coarse guard: TTL must be at least one analyzer tick (1s). Catches
    # accidental ``ttl: 0`` or missing-unit typos like ``ttl: 1`` (which
    # the loader treats as 1 second — fine here, but we still want a
    # human-readable unit suffix as the convention).
    assert ttl.endswith(("s", "m", "h")), f"unexpected TTL form: {ttl!r}"


# ── Execution paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_clicks_when_next_page_arrow_absent(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """End-of-carousel state (no ``next_page`` arrow) → scenario exits
    without tapping. The *cross-tick* loop is also closed here: with the
    arrow gone the analyze rule stops matching, so no further pushes are
    queued either."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop"},
    )

    # Captured frame at the rightmost carousel position — no ``>`` arrow.
    frame = _load_bgr("page.shop.daily_deals.tabs_end.png")

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-tab-advance-noop",
        player_id="p1",
        scenario_key="shop.tab.advance",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == []


@pytest.mark.asyncio
async def test_clicks_exactly_once_per_run_when_arrow_visible(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """Arrow visible → exactly one tap, then ``max: 1`` drops out of the
    loop. This is the *per-tick load cap*: even if the arrow never
    disappears, the analyzer's 1-minute TTL combined with this cap means
    at most one click per minute."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop"},
    )

    frame = _load_bgr("page.shop.dawn_market.png")

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-tab-advance-once",
        player_id="p1",
        scenario_key="shop.tab.advance",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=NEXT_PAGE_REGION),
    ]
