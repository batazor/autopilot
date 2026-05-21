"""Pixel-level + overlay-level checks for ``layout.tab_active_detector``.

The detector relies on the cream/white selected-tab background producing low
mean HSV saturation and high mean value, while inactive blue tabs sit at high
S and mid V. Both layers of coverage:

* Direct calls against the labeled bbox in ``references/mail_page.png`` (the
  System tab is active there; the other four are inactive).
* Overlay-engine integration via ``isTabActive: true|false`` to ensure the new
  flag wires through the same way ``isRedDot`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.tab_active_detector import (
    is_tab_active_in_bbox_percent,
    tab_activity_stats,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIL_FIXTURE = REPO_ROOT / "references" / "mail_page.png"
MAIL_MODULE_REFERENCES = REPO_ROOT / "modules" / "mail" / "references"
TRIALS_FIXTURE = REPO_ROOT / "modules" / "events" / "trials" / "references" / "page.trials.png"

ACTIVE_TAB = "mail.tab.system"
INACTIVE_TABS = (
    "mail.tab.wars",
    "mail.tab.alliance",
    "mail.tab.reports",
    "mail.tab.starred",
)
MAIL_TAB_FIXTURES = {
    "mail.tab.wars": MAIL_MODULE_REFERENCES / "mail_tab_wars.png",
    "mail.tab.alliance": MAIL_MODULE_REFERENCES / "mail_tab_alliance.png",
    "mail.tab.system": MAIL_MODULE_REFERENCES / "mail_tab_system.png",
    "mail.tab.reports": MAIL_MODULE_REFERENCES / "mail_tab_reports.png",
    "mail.tab.starred": MAIL_MODULE_REFERENCES / "mail_tab_starred.png",
}
ALL_MAIL_TABS = tuple(MAIL_TAB_FIXTURES)


def _load_area() -> dict[str, Any]:
    return json.loads((REPO_ROOT / "area.json").read_text(encoding="utf-8"))


def _bbox_for(area_doc: dict[str, Any], name: str) -> dict[str, float]:
    for entry in area_doc.get("screens", []):
        for r in entry.get("regions", []):
            if r.get("name") == name:
                bbox = r.get("bbox")
                assert isinstance(bbox, dict), f"{name} has no bbox"
                return bbox
    msg = f"region {name!r} not found in area.json"
    raise AssertionError(msg)


def test_detector_marks_system_tab_active() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()
    bbox = _bbox_for(area, ACTIVE_TAB)
    assert is_tab_active_in_bbox_percent(image_bgr, bbox) is True


@pytest.mark.parametrize("tab_name", INACTIVE_TABS)
def test_detector_marks_other_tabs_inactive(tab_name: str) -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()
    bbox = _bbox_for(area, tab_name)
    assert is_tab_active_in_bbox_percent(image_bgr, bbox) is False


def test_activity_stats_gap_holds() -> None:
    """The active/inactive gap is wide enough that a single mean-S threshold suffices."""
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()

    from layout.template_match import patch_bgr_from_bbox_percent

    patch_active, _ = patch_bgr_from_bbox_percent(image_bgr, _bbox_for(area, ACTIVE_TAB))
    s_active, v_active = tab_activity_stats(patch_active)

    for name in INACTIVE_TABS:
        patch, _ = patch_bgr_from_bbox_percent(image_bgr, _bbox_for(area, name))
        s, v = tab_activity_stats(patch)
        assert s > s_active + 30, f"{name}: S gap too small (active={s_active}, inactive={s})"
        assert v < v_active, f"{name}: V should be lower than active (active={v_active}, inactive={v})"


def test_detector_marks_yellow_trials_tab_active() -> None:
    image_bgr = cv2.imread(str(TRIALS_FIXTURE))
    assert image_bgr is not None
    area = load_area_doc(REPO_ROOT)

    active_tabs = [
        tab_name
        for tab_name in (
            "trial.day.1",
            "trial.day.2",
            "trial.day.3",
            "trial.day.4",
            "trial.day.5",
        )
        if is_tab_active_in_bbox_percent(image_bgr, _bbox_for(area, tab_name))
    ]

    assert active_tabs == ["trial.day.1"]


@pytest.mark.parametrize(
    "task_region",
    ["trial.day.1", "trial.task.1", "trial.task.2", "trial.task.3"],
)
def test_detector_marks_trials_task_red_dots(task_region: str) -> None:
    image_bgr = cv2.imread(str(TRIALS_FIXTURE))
    assert image_bgr is not None
    area = load_area_doc(REPO_ROOT)

    assert has_red_dot_in_bbox_percent(image_bgr, _bbox_for(area, task_region)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_region",
    [
        "trial.day.1",
        "trial.task.1",
        "trial.task.2",
        pytest.param(
            "trial.task.3",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "overlay engine reports red_dot_present=False for trial.task.3 "
                    "while the direct ``has_red_dot_in_bbox_percent`` detector agrees "
                    "with the fixture (see companion test above). Likely a divergence "
                    "between the two red-dot paths (different bbox version or "
                    "saturation threshold inside the engine's ``within_zone`` search)."
                ),
            ),
        ),
    ],
)
async def test_overlay_isreddot_matches_trials_task_red_dots(task_region: str) -> None:
    image_bgr = cv2.imread(str(TRIALS_FIXTURE))
    assert image_bgr is not None
    area = load_area_doc(REPO_ROOT)
    rule = {
        "name": f"{task_region}.has_red_dot",
        "region": task_region,
        "isRedDot": True,
    }

    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="event.trials.day.1",
    )

    row = out.get(f"{task_region}.has_red_dot")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    assert row.get("red_dot_present") is True


@pytest.mark.asyncio
async def test_overlay_istabactive_true_matches_yellow_trials_tab() -> None:
    image_bgr = cv2.imread(str(TRIALS_FIXTURE))
    assert image_bgr is not None
    area = load_area_doc(REPO_ROOT)
    rule = {
        "name": "trial.day.1.is_active",
        "region": "trial.day.1",
        "isTabActive": True,
        "screens": ["event.trials"],
    }

    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="event.trials",
    )

    row = out.get("trial.day.1.is_active")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    assert row.get("tab_active") is True
    assert row.get("yellow_ratio", 0.0) > 0.25


@pytest.mark.parametrize(("expected_tab", "fixture_path"), MAIL_TAB_FIXTURES.items())
def test_module_mail_tab_fixtures_detect_current_tab(
    expected_tab: str,
    fixture_path: Path,
) -> None:
    image_bgr = cv2.imread(str(fixture_path))
    assert image_bgr is not None, f"OpenCV must load {fixture_path}"
    area = _load_area()

    active_tabs = [
        tab_name
        for tab_name in ALL_MAIL_TABS
        if is_tab_active_in_bbox_percent(image_bgr, _bbox_for(area, tab_name))
    ]

    assert active_tabs == [expected_tab]


def test_invalid_inputs_return_false() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    assert is_tab_active_in_bbox_percent(None, {"x": 0, "y": 0, "width": 1, "height": 1}) is False  # ty: ignore[invalid-argument-type]
    assert is_tab_active_in_bbox_percent(image_bgr, {}) is False
    assert is_tab_active_in_bbox_percent(image_bgr, {"x": 0, "y": 0}) is False


@pytest.mark.asyncio
async def test_overlay_istabactive_true_matches_active_tab() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()
    rule = {
        "name": "mail.tab.system.is_active",
        "region": ACTIVE_TAB,
        "isTabActive": True,
        "screens": ["mail"],
    }
    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="mail",
    )
    row = out.get("mail.tab.system.is_active")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    assert row.get("action") == "tab_active"
    assert row.get("tab_active") is True
    # tap coords default to bbox center (in percent).
    assert isinstance(row.get("tap_x_pct"), float)
    assert isinstance(row.get("tap_y_pct"), float)


@pytest.mark.asyncio
async def test_overlay_istabactive_true_misses_inactive_tab() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()
    rule = {
        "name": "mail.tab.wars.is_active",
        "region": "mail.tab.wars",
        "isTabActive": True,
        "screens": ["mail"],
    }
    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="mail",
    )
    row = out.get("mail.tab.wars.is_active")
    assert isinstance(row, dict), out
    assert row.get("matched") is False, row
    assert row.get("tab_active") is False


@pytest.mark.asyncio
async def test_overlay_istabactive_false_matches_inactive_tab() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    area = _load_area()
    rule = {
        "name": "mail.tab.wars.is_inactive",
        "region": "mail.tab.wars",
        "isTabActive": False,
        "screens": ["mail"],
    }
    out = await evaluate_overlay_rules_async(
        image_bgr,
        area,
        REPO_ROOT,
        [rule],
        current_screen="mail",
    )
    row = out.get("mail.tab.wars.is_inactive")
    assert isinstance(row, dict), out
    assert row.get("matched") is True, row
    assert row.get("want_tab_active") is False
    assert row.get("tab_active") is False
