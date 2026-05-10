"""Overlay rules with ``isRedDot: true|false`` (programmatic badge detection)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_CITY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "main_city_v2_red_dots.png"


@pytest.mark.asyncio
async def test_isworkers_visible_matches_when_dot_present() -> None:
    image_bgr = cv2.imread(str(MAIN_CITY_FIXTURE))
    assert image_bgr is not None

    area_doc: dict[str, Any] = json.loads((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    rule = {
        "name": "isWorkers.visible",
        "region": "isWorkers",
        "isRedDot": True,
        "screens": ["main_city"],
    }
    out = await evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    row = out.get("isWorkers.visible")
    assert isinstance(row, dict)
    assert row.get("matched") is True, row
    assert row.get("action") == "red_dot"
    assert row.get("red_dot_present") is True


@pytest.mark.asyncio
async def test_isworkers_visible_no_match_on_blank_screen() -> None:
    image_bgr = cv2.imread(str(MAIN_CITY_FIXTURE))
    assert image_bgr is not None
    blank = image_bgr * 0 + 64

    area_doc: dict[str, Any] = json.loads((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    rule = {
        "name": "isWorkers.visible",
        "region": "isWorkers",
        "isRedDot": True,
        "screens": ["main_city"],
    }
    out = await evaluate_overlay_rules_async(
        blank,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    row = out.get("isWorkers.visible")
    assert isinstance(row, dict)
    assert row.get("matched") is False, row
    assert row.get("red_dot_present") is False


@pytest.mark.asyncio
async def test_isreddot_false_matches_when_dot_absent() -> None:
    image_bgr = cv2.imread(str(MAIN_CITY_FIXTURE))
    assert image_bgr is not None
    blank = image_bgr * 0 + 64

    area_doc: dict[str, Any] = json.loads((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    rule = {
        "name": "workers.quiet",
        "region": "isWorkers",
        "isRedDot": False,
        "screens": ["main_city"],
    }
    out = await evaluate_overlay_rules_async(
        blank,
        area_doc,
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    row = out.get("workers.quiet")
    assert isinstance(row, dict)
    assert row.get("matched") is True, row
    assert row.get("want_dot_present") is False
