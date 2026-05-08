from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from layout.types import Region
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OCRResult


class _FakeOcrClient:
    def __init__(self) -> None:
        self.regions: list[Region] = []
        self.text = "Arena"

    async def ocr_regions(self, _image: np.ndarray, regions: list[Region]) -> list[OCRResult]:
        self.regions = regions
        return [
            OCRResult(region_id=f"r{i}", text=self.text, confidence=0.99)
            for i, _ in enumerate(regions)
        ]


@pytest.mark.asyncio
async def test_screen_detector_uses_yaml_landmarks(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  arena:
    landmarks:
      - ocr: page_title
        contains: [arena]
        threshold: 0.8
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(screen_graph, "_screen_verify_yaml_path", lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()

    detector = ScreenDetector()
    fake_ocr = _FakeOcrClient()
    detector._client = fake_ocr
    detector._area_doc = {
        "screens": [
            {
                "screen_id": "arena",
                "regions": [
                    {
                        "name": "page_title",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 10},
                    }
                ],
            }
        ]
    }

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert detected == ScreenName.ARENA
    assert fake_ocr.regions == [Region(10, 40, 30, 20)]


@pytest.mark.asyncio
async def test_screen_detector_switches_on_page_title_text(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
text_switch:
  - ocr: page_title
    threshold: 0.8
    cases:
      arena: [arena]
screens:
  main_city:
    landmarks:
      - ocr: is_main_city
        contains: [city]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(screen_graph, "_screen_verify_yaml_path", lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()

    detector = ScreenDetector()
    fake_ocr = _FakeOcrClient()
    detector._client = fake_ocr
    detector._area_doc = {
        "screens": [
            {
                "screen_id": "common",
                "regions": [
                    {
                        "name": "page_title",
                        "bbox": {"x": 11, "y": 2, "width": 70, "height": 5},
                    },
                    {
                        "name": "is_main_city",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 10},
                    },
                ],
            }
        ]
    }

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert detected == ScreenName.ARENA
    assert fake_ocr.regions == [Region(11, 4, 70, 10)]


@pytest.mark.asyncio
async def test_screen_detector_uses_match_landmark(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  main_city:
    landmarks:
      - match: isNewPeople
        threshold: 0.98
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(screen_graph, "_screen_verify_yaml_path", lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Path,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        name = str(rules[0]["name"])
        return {name: {"matched": True}}

    import navigation.detector as detector_module

    monkeypatch.setattr(
        detector_module,
        "evaluate_overlay_rules_async",
        evaluate_overlay_rules_async,
    )
    detector = ScreenDetector()
    detector._area_doc = {"screens": []}

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert detected == ScreenName.MAIN_CITY
