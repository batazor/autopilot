"""``action: text`` rules use only the primary bbox after `_search` removal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest

from analysis import overlay_engine
from config.loader import get_settings
from ocr.client import OcrClient

REPO_ROOT = Path(__file__).resolve().parents[2]
PATRICK_HERO_CARD_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "bs1_current_state_tapanywhere.png"
)
CHAPTER_REWARDS_REFERENCE = REPO_ROOT / "references" / "tapanywhereyoexit.png"


@pytest.mark.asyncio
async def test_text_rule_does_not_fall_back_to_removed_search_bbox() -> None:
    """Patrick hero-card popup no longer relies on a `_search` auxiliary bbox."""
    img = cv2.imread(str(PATRICK_HERO_CARD_FIXTURE))
    assert img is not None
    area_doc: dict[str, Any] = json.loads(
        (REPO_ROOT / "area.json").read_text(encoding="utf-8")
    )
    rule = {
        "name": "tapanywhereyoexit.visible",
        "region": "tapanywhereyoexit",
        "action": "text",
        "expected": ["tap anywhere"],
        "threshold": 0.7,
    }

    ocr = OcrClient(get_settings())
    out = await overlay_engine.evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], ocr_client=ocr
    )
    row = out.get("tapanywhereyoexit.visible")
    assert isinstance(row, dict)
    assert row.get("matched") is False, row
    assert row.get("ocr_source") == "tapanywhereyoexit"


@pytest.mark.asyncio
async def test_text_rule_uses_primary_bbox_when_it_already_matches() -> None:
    """Chapter Rewards reference: the prompt sits squarely inside the primary
    ``tapanywhereyoexit`` bbox. The engine must succeed without ever falling
    back to ``_search`` — otherwise we'd waste an OCR call on every frame that
    already matched the cheap way.
    """
    img = cv2.imread(str(CHAPTER_REWARDS_REFERENCE))
    assert img is not None
    area_doc: dict[str, Any] = json.loads(
        (REPO_ROOT / "area.json").read_text(encoding="utf-8")
    )
    rule = {
        "name": "tapanywhereyoexit.visible",
        "region": "tapanywhereyoexit",
        "action": "text",
        "expected": ["tap anywhere"],
        "threshold": 0.7,
    }

    ocr = OcrClient(get_settings())
    out = await overlay_engine.evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], ocr_client=ocr
    )
    row = out.get("tapanywhereyoexit.visible")
    assert isinstance(row, dict)
    assert row.get("matched") is True, row
    assert row.get("ocr_source") == "tapanywhereyoexit"
