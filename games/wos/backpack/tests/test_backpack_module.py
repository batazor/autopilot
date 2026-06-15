"""Regression tests for the backpack module (references, overlay, templates, DSL)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import ANY, call

import cv2
import pytest

if TYPE_CHECKING:
    import numpy as np
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from analysis.overlay import run_overlay_analysis_sync
from analysis.overlay_area import default_area_doc_for_overlay
from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_analyze_yaml
from dsl import template_resolver
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.tab_active_detector import is_tab_active_in_bbox_percent

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
MAIN_CITY_FIXTURE = REFERENCES_DIR / "main_city_vip.png"
BACKPACK_PAGE_FIXTURE = REFERENCES_DIR / "page.backpack.png"

ALL_BACKPACK_TABS = (
    "page.backpack.resources",
    "page.backpack.speedup",
    "page.backpack.bonus",
    "page.backpack.gear",
    "page.backpack.other",
)
ACTIVE_TAB = "page.backpack.resources"
TABS_WITH_RED_DOT = (
    "page.backpack.speedup",
    "page.backpack.other",
)
TABS_WITHOUT_RED_DOT = (
    "page.backpack.resources",
    "page.backpack.bonus",
    "page.backpack.gear",
)
TAB_SCENARIO_KEYS = tuple(f"backpack.tab.{tab.rsplit('.', 1)[-1]}" for tab in ALL_BACKPACK_TABS)


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _bbox(region_name: str) -> dict[str, float]:
    area_doc = load_area_doc(REPO_ROOT)
    found = screen_region_by_name(area_doc, region_name)
    assert found is not None, f"missing region {region_name!r}"
    _screen, region = found
    bbox = region.get("bbox")
    assert isinstance(bbox, dict), f"{region_name} has no bbox"
    return bbox


def _backpack_overlay_rules() -> list[dict[str, Any]]:
    analyze = load_analyze_yaml(MODULE_DIR / "analyze" / "analyze.yaml")
    overlay = analyze.get("overlay")
    assert isinstance(overlay, list)
    return [r for r in overlay if isinstance(r, dict)]


def _has_red_dot_in_tab_bbox(image_bgr: np.ndarray, tab_name: str) -> bool:
    """Within-zone probe — strict labeled rectangle (no pad bleed from neighbors)."""
    return bool(
        has_red_dot_in_bbox_percent(
            image_bgr,
            _bbox(tab_name),
            pad_px=0,
            edge_badge_pad_ratio=0.0,
        )
    )


def _overlay_rule(name: str) -> dict[str, Any]:
    for rule in _backpack_overlay_rules():
        if rule.get("name") == name:
            return rule
    msg = f"missing overlay rule {name!r}"
    raise AssertionError(msg)


def test_literal_tab_scenario_copies_removed() -> None:
    for tab in ("resources", "speedup", "bonus", "gear", "other"):
        assert not (MODULE_DIR / "scenarios" / f"backpack.tab.{tab}.yaml").exists()
    assert not (MODULE_DIR / "scenarios" / "backpack.open.yaml").exists()


def test_backpack_open_scenario_is_registered_with_expected_shape(snapshot) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "backpack")
    assert loaded is not None
    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "backpack.yaml"
    assert doc == snapshot


@pytest.mark.parametrize("scenario_key", TAB_SCENARIO_KEYS)
def test_tab_template_renders_explicit_backpack_pages(snapshot, scenario_key: str) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, scenario_key)
    assert loaded is not None
    path, doc = loaded
    assert path.name == "backpack.tab.{tab}.yaml"
    assert doc == snapshot


def test_main_city_vip_backpack_entry_has_red_dot() -> None:
    image_bgr = _load_reference_bgr(MAIN_CITY_FIXTURE.name)
    assert has_red_dot_in_bbox_percent(image_bgr, _bbox("main_city.to.backpack")) is True


def test_page_backpack_resources_tab_is_active() -> None:
    image_bgr = _load_reference_bgr(BACKPACK_PAGE_FIXTURE.name)
    active_tabs = [
        tab_name
        for tab_name in ALL_BACKPACK_TABS
        if is_tab_active_in_bbox_percent(image_bgr, _bbox(tab_name))
    ]
    assert active_tabs == [ACTIVE_TAB]


@pytest.mark.asyncio
async def test_backpack_title_matches_page_fixture() -> None:
    image_bgr = _load_reference_bgr(BACKPACK_PAGE_FIXTURE.name)
    area = load_area_doc(REPO_ROOT)
    rule = {
        "name": "backpack.title.visible",
        "region": "backpack.title",
        "action": "exist",
        "threshold": 0.9,
    }

    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="backpack",
    )

    row = out.get("backpack.title.visible")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row


def test_backpack_screen_verify_uses_title_landmark() -> None:
    import navigation.screen_graph as screen_graph

    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        expected_base = [{"ocr": "page.common.title", "contains": "Backpack", "threshold": 0.8}]
        assert screen_graph.screen_landmark_rules("backpack") == expected_base
        assert screen_graph.screen_verify_rules("backpack") == expected_base
        expected_tab = [
            {
                "ocr": "page.common.title",
                "contains": "Backpack",
                "threshold": 0.8,
                "tab_active": ACTIVE_TAB,
            }
        ]
        assert screen_graph.screen_landmark_rules("backpack.resources") == expected_tab
        assert screen_graph.screen_verify_rules("backpack.resources") == expected_tab
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


@pytest.mark.parametrize("tab_name", TABS_WITHOUT_RED_DOT)
def test_page_backpack_tabs_without_notification_dots(tab_name: str) -> None:
    image_bgr = _load_reference_bgr(BACKPACK_PAGE_FIXTURE.name)
    assert _has_red_dot_in_tab_bbox(image_bgr, tab_name) is False


@pytest.mark.parametrize("tab_name", TABS_WITH_RED_DOT)
def test_page_backpack_tabs_with_notification_dots(tab_name: str) -> None:
    image_bgr = _load_reference_bgr(BACKPACK_PAGE_FIXTURE.name)
    assert _has_red_dot_in_tab_bbox(image_bgr, tab_name) is True


@pytest.mark.asyncio
async def test_overlay_main_city_entry_visible_on_vip_fixture() -> None:
    image_bgr = _load_reference_bgr(MAIN_CITY_FIXTURE.name)
    area = load_area_doc(REPO_ROOT)
    rule = _overlay_rule("backpack.main_city.entry.visible")

    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    row = out.get("backpack.main_city.entry.visible")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    assert row.get("red_dot_present") is True


@pytest.mark.asyncio
async def test_overlay_main_city_entry_pushes_backpack_scenario() -> None:
    image_bgr = _load_reference_bgr(MAIN_CITY_FIXTURE.name)
    area = load_area_doc(REPO_ROOT)
    rule = _overlay_rule("backpack.main_city.entry.visible")

    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    row = out.get("backpack.main_city.entry.visible")
    assert isinstance(row, dict)
    push = row.get("pushScenario")
    assert isinstance(push, list) and push
    assert push[0].get("type") == "backpack"


def test_backpack_tab_strip_red_dot_pushes_tab_scenarios() -> None:
    """The dynamic tab-strip scan replaces the five hard-coded per-tab red-dot
    rules: it re-anchors the grid every tick and enqueues a claim scenario for
    every dotted, non-active tab (left-to-right), skipping the active one whose
    own scenario clears its dot.

    On the reference, ``Resources`` is active and ``Speedup`` + ``Other`` carry
    dots, so exactly those two pages are pushed.
    """
    image_bgr = _load_reference_bgr(BACKPACK_PAGE_FIXTURE.name)

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        area_doc=default_area_doc_for_overlay(REPO_ROOT),
        current_screen="backpack",
    )

    row = out.get("backpack.tabs.visible_red_dot")
    assert isinstance(row, dict), out
    assert row["matched"] is True
    assert row["active_page_id"] == "backpack.tab.resources"
    assert row["red_dot_pages"] == ["backpack.tab.speedup", "backpack.tab.other"]
    assert row["pushScenario"] == [
        {
            "type": "backpack.tab.speedup",
            "priority": None,
            "ttl": 300,
            "dsl_scenario": None,
        },
        {
            "type": "backpack.tab.other",
            "priority": None,
            "ttl": 300,
            "dsl_scenario": None,
        },
    ]


@pytest.mark.asyncio
async def test_backpack_scenario_taps_entry_from_main_city_fixture(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "main_city"},
    )

    main_city = _load_reference_bgr(MAIN_CITY_FIXTURE.name)
    actions = make_actions([main_city, main_city])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="backpack-open-test",
        player_id="p1",
        scenario_key="backpack",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region="main_city.to.backpack"),
    ]
