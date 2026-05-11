"""Unit checks for the ``isTabActive:`` filter on DSL ``match:`` steps.

End-to-end Redis-backed coverage is intentionally skipped here — the cross-cut
between ``match:`` → ``click:`` is already proven by ``test_dsl_is_red_dot``;
this module pins down the new pieces:

* parser (``_step_tab_active_requirement``) — bool + alias coercion;
* short-circuit row builder (``_build_tab_active_only_row``) — match / miss /
  unexpected-active outcomes against a real mail-screen fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2

import tasks.dsl_scenario as dsl

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIL_FIXTURE = REPO_ROOT / "references" / "mail_page.png"


def _load_bbox(name: str) -> dict[str, float]:
    doc: dict[str, Any] = json.loads((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    for entry in doc.get("screens", []):
        for r in entry.get("regions", []):
            if r.get("name") == name:
                bbox = r.get("bbox")
                assert isinstance(bbox, dict)
                return bbox
    raise AssertionError(f"region {name!r} not found in area.json")


def test_step_tab_active_requirement_reads_bool_and_aliases() -> None:
    assert dsl._step_tab_active_requirement({"isTabActive": True}) is True
    assert dsl._step_tab_active_requirement({"isTabActive": False}) is False
    assert dsl._step_tab_active_requirement({"is_tab_active": True}) is True
    assert dsl._step_tab_active_requirement({"isTabActive": "yes"}) is True
    assert dsl._step_tab_active_requirement({"isTabActive": "off"}) is False
    assert dsl._step_tab_active_requirement({}) is None
    assert dsl._step_tab_active_requirement({"isTabActive": "maybe"}) is None


def test_build_tab_active_only_row_matches_active_tab() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    bbox = _load_bbox("mail.tab.system")
    region_def = {"name": "mail.tab.system", "bbox": bbox}

    out = dsl.DslScenarioTask._build_tab_active_only_row(
        region="mail.tab.system",
        region_def=region_def,
        image_bgr=image_bgr,
        requirement=True,
    )
    assert out["matched"] is True
    assert out["tab_active"] is True
    assert out["tab_active_required"] is True
    # Tap point falls back to bbox center.
    expected_x = bbox["x"] + bbox["width"] / 2.0
    expected_y = bbox["y"] + bbox["height"] / 2.0
    assert out["tap_x_pct"] == expected_x
    assert out["tap_y_pct"] == expected_y


def test_build_tab_active_only_row_misses_inactive_tab() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    bbox = _load_bbox("mail.tab.wars")
    region_def = {"name": "mail.tab.wars", "bbox": bbox}

    out = dsl.DslScenarioTask._build_tab_active_only_row(
        region="mail.tab.wars",
        region_def=region_def,
        image_bgr=image_bgr,
        requirement=True,
    )
    assert out["matched"] is False
    assert out["tab_active"] is False
    assert out["reason"] == "tab_inactive"


def test_build_tab_active_only_row_misses_when_unexpectedly_active() -> None:
    image_bgr = cv2.imread(str(MAIL_FIXTURE))
    assert image_bgr is not None
    bbox = _load_bbox("mail.tab.system")
    region_def = {"name": "mail.tab.system", "bbox": bbox}

    out = dsl.DslScenarioTask._build_tab_active_only_row(
        region="mail.tab.system",
        region_def=region_def,
        image_bgr=image_bgr,
        requirement=False,
    )
    assert out["matched"] is False
    assert out["reason"] == "tab_active_unexpected"


def test_build_tab_active_only_row_errors_without_bbox() -> None:
    out = dsl.DslScenarioTask._build_tab_active_only_row(
        region="mail.tab.system",
        region_def={"name": "mail.tab.system"},
        image_bgr=cv2.imread(str(MAIL_FIXTURE)),
        requirement=True,
    )
    assert out["matched"] is False
    assert out["reason"] == "missing_bbox_for_tab_active"
