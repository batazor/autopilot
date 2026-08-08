from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient, OCRResult

if TYPE_CHECKING:
    from pathlib import Path

    from layout.types import Region


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
@pytest.mark.asyncio
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    # Isolate from the per-building synthesis (reads db/buildings directly) — the
    # always-match-rules[0] mock below would otherwise resolve to a building node.
    mocker.patch.object(screen_graph, "_building_screen_defs", new=lambda *_a, **_k: [])
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
async def test_screen_detector_returns_yaml_screen_not_in_import_time_enum(
    mocker,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  hot_added:
    landmarks:
      - match: hot.added.title
        threshold: 0.9
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    # Isolate from the per-building synthesis (reads db/buildings directly) — the
    # always-match-rules[0] mock below would otherwise resolve to a building node.
    mocker.patch.object(screen_graph, "_building_screen_defs", new=lambda *_a, **_k: [])
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
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == "hot_added"


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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
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
    import config.module_discovery as module_discovery
    import config.paths as paths
    import layout.area_manifest as area_manifest

    mocker.patch.object(paths, "repo_root", new=lambda: tmp_path)
    module_discovery._clear_module_discovery_caches()
    area_manifest.clear_area_doc_cache()
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
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
