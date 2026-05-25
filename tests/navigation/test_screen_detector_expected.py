"""Routing-aware screen detection: ``expected`` probes the hop destination first."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_detect_screen_probes_expected_before_priority_list(
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
  mail:
    priority: 30
    landmarks:
      - match: mail.title
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    mocker.patch.object(
        screen_graph,
        "screen_verify_screen_names",
        return_value=["loading", "mail"],
    )
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    probe_order: list[str] = []

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        name = str(rules[0]["name"])
        if "mail" in name:
            probe_order.append("mail")
            return {name: {"matched": True}}
        if "loading" in name:
            probe_order.append("loading")
        return {name: {"matched": False}}

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
            expected="mail",
        )
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert detected == ScreenName.MAIL
    assert probe_order == ["mail"]


def test_merge_screen_probe_order_prepends_without_duplicates() -> None:
    out = ScreenDetector._merge_screen_probe_order(
        ["loading", "mail", "vip"],
        try_first=["mail", "loading"],
    )
    assert out == ["mail", "loading", "vip"]
