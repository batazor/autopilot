from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from analysis import overlay_engine
from layout.types import Region
from ocr.client import OCRResult


@pytest.mark.asyncio
async def test_overlay_action_text_attaches_push_scenario(monkeypatch: Any) -> None:
    """Regression: worker enqueues overlay pushes from ``payload['pushScenario']``.
    Text rules must attach ``pushScenario`` like findIcon/color_check do.
    """
    repo_root = Path(__file__).resolve().parents[1]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [
            {"name": "chapter_task_router", "priority": 70000, "ttl": "20s"},
        ],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region) -> OCRResult:
            return OCRResult(region_id="r0", text="Bunk Beds in Shelter 2", confidence=0.95)

    monkeypatch.setattr(overlay_engine, "OcrClient", lambda *a, **k: _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="main_city",
        rule_eval_state=None,
    )
    row = out.get("chapter.task.present")
    assert isinstance(row, dict)
    assert row.get("matched") is True
    pu = row.get("pushScenario")
    assert isinstance(pu, list) and len(pu) >= 1
    assert pu[0].get("type") == "chapter_task_router"


@pytest.mark.asyncio
async def test_overlay_action_text_skipped_when_screen_not_allowed(monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [{"name": "chapter_task_router", "priority": 70000}],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region) -> OCRResult:
            raise AssertionError("OCR must not run when screen gate fails")

    monkeypatch.setattr(overlay_engine, "OcrClient", lambda *a, **k: _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="mail",
        rule_eval_state=None,
    )
    assert "chapter.task.present" not in out


@pytest.mark.asyncio
async def test_overlay_screen_gate_is_case_insensitive(monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [{"name": "chapter_task_router", "priority": 70000}],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region) -> OCRResult:
            return OCRResult(region_id="r0", text="x", confidence=0.95)

    monkeypatch.setattr(overlay_engine, "OcrClient", lambda *a, **k: _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="MAIN_CITY",
        rule_eval_state=None,
    )
    row = out.get("chapter.task.present")
    assert isinstance(row, dict)
    assert row.get("matched") is True
