"""Screen detector batches landmark rules and resolves matches in priority order."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_detect_by_match_landmarks_batches_and_resolves_in_priority_order(
    mocker,
    tmp_path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  loading:
    priority: 1
    landmarks:
      - match: text.survival
  main_city:
    priority: 10
    landmarks:
      - match: icon.world
  mail:
    priority: 30
    landmarks:
      - match: mail.title
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    import navigation.detector as detector_module

    mocker.patch.object(
        detector_module,
        "screen_verify_screen_names",
        new=lambda: ["loading", "main_city", "mail"],
    )
    mocker.patch.object(
        detector_module,
        "screen_verify_parent",
        new=lambda _s: None,
    )
    detector_module.ScreenDetector._landmark_rules_cache.clear()
    detector_module.ScreenDetector._landmark_rules_cache_fp = None
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    batch_sizes: list[int] = []

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        batch_sizes.append(len(rules))
        return {
            str(rule["name"]): {"matched": "main_city" in str(rule["name"])}
            for rule in rules
        }

    mocker.patch.object(
        detector_module,
        "evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._area_doc = {"screens": []}

    try:
        detected = await detector._detect_by_match_landmarks(
            np.zeros((200, 100, 3), dtype=np.uint8),
        )
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.MAIN_CITY
    # All unparented screens are batched in one call now (down from N
    # per-screen calls). Priority order still picks ``main_city`` over the
    # non-matching ``loading`` and the unevaluated-by-mock ``mail``.
    assert len(batch_sizes) == 1
    # Batch contains every unique landmark across the three test screens
    # (``text.survival`` + ``icon.world`` + ``mail.title``, plus
    # ``main_city.title`` merged from area.json's ``screen_region``).
    assert batch_sizes[0] >= 3


def test_screen_verify_order_names_sorts_by_priority() -> None:
    ordered = screen_graph.screen_verify_order_names(
        ["mail", "loading", "main_city"],
    )
    assert ordered[0] == "loading"
    assert ordered[-1] == "mail"
