"""Parent gating in ScreenDetector._detect_by_match_landmarks.

A child screen (e.g. ``mail.wars``) is strictly a sub-view of its parent
(``mail``). When the parent's anchor template doesn't fire on the frame, the
child cannot match either — the detector must skip the child without running
its unique ``tab_active`` template match.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.detector as detector_module
import navigation.screen_graph as screen_graph
from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_parent_gate_skips_children_when_parent_anchor_absent(
    mocker,
    tmp_path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  mail.wars:
    parent: mail
    priority: 15
    landmarks:
      - match: mail.title
        tab_active: mail.tab.wars
  mail.alliance:
    parent: mail
    priority: 15
    landmarks:
      - match: mail.title
        tab_active: mail.tab.alliance
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
        return_value=["mail.wars", "mail.alliance", "mail"],
    )
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    evaluated_regions: list[str] = []

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for rule in rules:
            region = str(rule.get("region") or "")
            evaluated_regions.append(region)
            # Parent anchor (``mail.title``) is absent — frame is not on mail at all.
            out[str(rule["name"])] = {"matched": False, "region": region}
        return out

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

    assert detected == ScreenName.UNKNOWN
    # Parent gate runs ``mail.title`` exactly once and finds it negative; both
    # mail.* children are skipped without their unique ``tab_active`` regions
    # being template-matched. Other regions appearing in the trace come from
    # ``_area_screen_region_landmarks`` and are unrelated to the optimization.
    assert "mail.title" in evaluated_regions
    assert evaluated_regions.count("mail.title") == 1
    assert "mail.tab.wars" not in evaluated_regions
    assert "mail.tab.alliance" not in evaluated_regions


@pytest.mark.asyncio
async def test_parent_gate_lets_children_match_when_anchor_present(
    mocker,
    tmp_path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  mail.wars:
    parent: mail
    priority: 15
    landmarks:
      - match: mail.title
        tab_active: mail.tab.wars
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
        return_value=["mail.wars", "mail"],
    )
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            str(rule["name"]): {"matched": True, "region": rule.get("region")}
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

    assert detected == ScreenName.MAIL_WARS


def test_screen_verify_parent_returns_configured_value(mocker, tmp_path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  mail.wars:
    parent: mail
    rules:
      - match: mail.title
  mail:
    rules:
      - match: mail.title
  loading:
    rules:
      - match: text.survival
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.screen_verify_parent("mail.wars") == "mail"
        assert screen_graph.screen_verify_parent("mail") is None
        assert screen_graph.screen_verify_parent("loading") is None
        assert screen_graph.screen_verify_parent("nonexistent") is None
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
