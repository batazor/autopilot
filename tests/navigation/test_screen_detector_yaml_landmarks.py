from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from layout.types import Region
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient, OCRResult

if TYPE_CHECKING:
    from pathlib import Path


class _FakeOcrClient:
    def __init__(self) -> None:
        self.regions: list[Region] = []
        self.text = "Arena"

    async def ocr_regions(self, _image: np.ndarray, regions: list[Region], **_kwargs: Any) -> list[OCRResult]:
        self.regions = regions
        return [
            OCRResult(region_id=f"r{i}", text=self.text, confidence=0.99)
            for i, _ in enumerate(regions)
        ]


@pytest.mark.asyncio
@pytest.mark.skip(reason="legacy OCR landmark coverage; rewrite for template landmarks")
async def test_screen_detector_uses_yaml_landmarks(mocker, tmp_path: Path) -> None:
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    detector = ScreenDetector(OcrClient(get_settings()))
    fake_ocr = _FakeOcrClient()
    detector._client = fake_ocr  # ty: ignore[invalid-assignment]
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
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.ARENA
    assert fake_ocr.regions == [Region(10, 40, 30, 20)]


@pytest.mark.asyncio
@pytest.mark.skip(reason="legacy text_switch detector coverage; rewrite for template landmarks")
async def test_screen_detector_switches_on_page_title_text(
    mocker,
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    detector = ScreenDetector(OcrClient(get_settings()))
    fake_ocr = _FakeOcrClient()
    detector._client = fake_ocr  # ty: ignore[invalid-assignment]
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
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.ARENA
    assert fake_ocr.regions == [Region(11, 4, 70, 10)]


@pytest.mark.asyncio
async def test_screen_detector_uses_match_landmark(
    mocker,
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

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

    mocker.patch.object(
        detector_module,
        "evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._area_doc = {"screens": []}

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.MAIN_CITY


@pytest.mark.asyncio
async def test_screen_detector_requires_combined_match_and_tab_active(
    mocker,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  mail.alliance:
    landmarks:
      - match: mail.title
        threshold: 0.9
        tab_active: mail.tab.alliance
  mail:
    landmarks:
      - match: mail.title
        threshold: 0.9
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Path,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            str(rule["name"]): {
                "matched": str(rule["region"]) == "mail.tab.alliance",
            }
            for rule in rules
        }

    import navigation.detector as detector_module

    mocker.patch.object(
        detector_module,
        "evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._area_doc = {"screens": []}

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.UNKNOWN


@pytest.mark.asyncio
async def test_sticky_hint_allows_prior_overlay_to_preempt(
    mocker,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    area = tmp_path / "area.json"
    area.write_text('{"screens":[]}', encoding="utf-8")
    cfg.write_text(
        """
screens:
  main_city:
    priority: 10
    landmarks:
      - match: icon.world
  welcome_back:
    priority: 100
    landmarks:
      - match: text.welcome_back
    rules:
      - match: text.welcome_back
  reconnect:
    priority: 100
    landmarks:
      - match: icon.reconnect
    rules:
      - match: icon.reconnect
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    mocker.patch.object(screen_graph, "_area_json_path", new=lambda: area)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Path,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {str(rule["name"]): {"matched": True} for rule in rules}

    import navigation.detector as detector_module

    mocker.patch.object(
        detector_module,
        "evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._area_doc = {"screens": []}

    try:
        detected = await detector.detect_screen(
            np.zeros((200, 100, 3), dtype=np.uint8),
            hint=ScreenName.RECONNECT,
        )
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.WELCOME_BACK
    assert detector.last_used_sticky_verify is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="legacy mocker-patched ScreenName coverage; rewrite for template landmarks")
async def test_screen_detector_can_return_building_from_match_landmark(
    mocker,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  building:
    landmarks:
      - match: page.building.furniture
        threshold: 0.85
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Path,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        name = str(rules[0]["name"])
        assert rules[0]["region"] == "page.building.furniture"
        return {name: {"matched": True}}

    import navigation.detector as detector_module

    mocker.patch.object(
        detector_module,
        "evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._area_doc = {"screens": []}

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.BUILDING
