"""``action: text`` rules fall back to ``{region}_search`` when the primary
bbox OCR fails to fuzzy-match ``expected``.

Mirrors the slide-find pattern that already exists for ``action: findIcon``:
a popup variant that moves the prompt out of the primary bbox (e.g. the
``tapanywhereyoexit`` text rendered at y=96 % on a hero-card popup vs y=90.76 %
on the original Chapter Rewards reference) still gets caught because the wider
``_search`` sibling bbox covers both positions and fuzzy ``partial`` matching
plucks the target phrase from the surrounding OCR noise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest

from analysis import overlay_engine
from config.loader import get_settings
from ocr.client import OcrClient

REPO_ROOT = Path(__file__).resolve().parents[1]
PATRICK_HERO_CARD_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "bs1_current_state_tapanywhere.png"
)
CHAPTER_REWARDS_REFERENCE = REPO_ROOT / "references" / "tapanywhereyoexit.png"


@pytest.mark.asyncio
async def test_text_rule_falls_back_to_search_bbox_when_primary_misses() -> None:
    """Patrick hero-card popup: primary bbox OCRs "Lv. 5" (the level row), but
    the ``_search`` auxiliary bbox covers the actual "Tap anywhere to continue"
    text 5 % below. The fallback must catch it; otherwise the popup never gets
    dismissed and the worker stalls.
    """
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
    assert row.get("matched") is True, row
    assert row.get("ocr_source") == "tapanywhereyoexit_search"
    match = row.get("match")
    assert isinstance(match, dict)
    assert match.get("candidate") == "tap anywhere"


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
