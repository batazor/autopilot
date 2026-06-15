"""Routing-aware screen detection: ``expected`` probes the hop destination first."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from layout.types import Region
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient, OCRResult


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
    import navigation.detector as detector_module

    mocker.patch.object(
        detector_module,
        "screen_verify_screen_names",
        new=lambda: ["loading", "mail"],
    )
    mocker.patch.object(
        detector_module,
        "screen_verify_parent",
        new=lambda _s: None,
    )
    detector_module.ScreenDetector._landmark_rules_cache.clear()
    detector_module.ScreenDetector._landmark_rules_cache_fp = None
    screen_graph.invalidate_screen_verify_config()

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # All landmark rules are batched in one call. ``mail.title`` matches;
        # ``text.survival`` does not. Resolution then picks ``mail`` first
        # because ``expected="mail"`` puts it at the head of the priority list.
        out: dict[str, Any] = {}
        for rule in rules:
            name = str(rule["name"])
            out[name] = {"matched": "mail" in name}
        return out

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


def test_merge_screen_probe_order_prepends_without_duplicates() -> None:
    out = ScreenDetector._merge_screen_probe_order(
        ["loading", "mail", "vip"],
        try_first=["mail", "loading"],
    )
    assert out == ["mail", "loading", "vip"]


@pytest.mark.asyncio
async def test_detect_screen_uses_ocr_landmarks_without_deduping_expected(
    mocker,
    tmp_path,
) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  wrong_page:
    priority: 10
    landmarks:
      - ocr: page.common.title
        contains: Wrong Page
        threshold: 0.8
  exploration:
    priority: 20
    landmarks:
      - ocr: page.common.title
        contains: Exploration
        threshold: 0.8
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    import navigation.detector as detector_module

    def screen_landmark_rules(screen: str) -> list[dict[str, Any]]:
        return {
            "wrong_page": [
                {
                    "ocr": "page.common.title",
                    "contains": "Wrong Page",
                    "threshold": 0.8,
                }
            ],
            "exploration": [
                {
                    "ocr": "page.common.title",
                    "contains": "Exploration",
                    "threshold": 0.8,
                }
            ],
        }.get(screen, [])

    mocker.patch.object(
        detector_module,
        "screen_verify_screen_names",
        new=lambda: ["wrong_page", "exploration"],
    )
    mocker.patch.object(
        detector_module,
        "screen_landmark_rules",
        new=screen_landmark_rules,
    )
    mocker.patch.object(
        detector_module,
        "screen_verify_config_fingerprint",
        new=lambda: ("test",),
    )
    mocker.patch.object(
        detector_module,
        "screen_verify_parent",
        new=lambda _s: None,
    )
    detector_module.ScreenDetector._landmark_rules_cache.clear()
    detector_module.ScreenDetector._landmark_rules_cache_fp = None
    screen_graph.invalidate_screen_verify_config()
    assert detector_module.screen_landmark_rules("exploration") == [
        {
            "ocr": "page.common.title",
            "contains": "Exploration",
            "threshold": 0.8,
        }
    ]
    compiled_rules, compiled_groups = detector_module.ScreenDetector._landmark_overlay_rules_for_screen(
        "exploration",
        name_prefix="test",
    )
    assert compiled_rules == [
        {
            "name": "test.exploration.page.common.title.ocr",
            "action": "text",
            "region": "page.common.title",
            "threshold": 0.8,
            "expected": ["Exploration"],
            "exact": True,
        }
    ]
    assert compiled_groups == [["test.exploration.page.common.title.ocr"]]

    class _FakeOcr:
        seen_regions: list[Region]

        def __init__(self) -> None:
            self.seen_regions = []

        async def ocr_regions(
            self,
            _image: np.ndarray,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            **_kwargs: Any,
        ) -> list[OCRResult]:
            self.seen_regions = regions
            return [
                OCRResult(
                    region_id=region_ids[i] if region_ids is not None else f"r{i}",
                    text="Exploration",
                    confidence=0.99,
                )
                for i, _region in enumerate(regions)
            ]

    fake_ocr = _FakeOcr()
    detector = ScreenDetector(fake_ocr)  # type: ignore[arg-type]
    detector._area_doc = {
        "screens": [
            {
                "screen_id": "",
                "regions": [
                    {
                        "name": "page.common.title",
                        "action": "text",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 10},
                    }
                ],
            }
        ]
    }

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.invalidate_screen_verify_config()

    assert str(detected) == "exploration"
    assert fake_ocr.seen_regions == [Region(10, 40, 30, 20), Region(10, 40, 30, 20)]


@pytest.mark.asyncio
async def test_sharded_scan_keeps_ocr_rules_in_one_batch(
    mocker,
    tmp_path,
) -> None:
    """On the parallel (sharded) path, every ``action: text`` rule must ride in
    a single ``ocr_regions`` call so the client's within-batch patch-hash dedup
    collapses the N screen-verify rules that all OCR the same title (e.g. 27×
    ``page.common.title``) into ONE Tesseract run. Striping them across shards
    would re-OCR the same bbox once per shard — concurrently, defeating both the
    batch dedup and the TTL patch cache (a backend stampede).
    """
    # Eight screens, each OCR-ing the SAME bbox with a distinct ``contains`` —
    # enough rules to cross ``_LANDMARK_PARALLEL_THRESHOLD`` and trigger sharding.
    words = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        "screens:\n"
        + "".join(
            f"  s{i}:\n"
            f"    priority: {10 + i}\n"
            f"    landmarks:\n"
            f"      - ocr: page.common.title\n"
            f"        contains: {w}\n"
            f"        threshold: 0.8\n"
            for i, w in enumerate(words)
        ),
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    import navigation.detector as detector_module

    def screen_landmark_rules(screen: str) -> list[dict[str, Any]]:
        for i, w in enumerate(words):
            if screen == f"s{i}":
                return [{"ocr": "page.common.title", "contains": w, "threshold": 0.8}]
        return []

    mocker.patch.object(
        detector_module,
        "screen_verify_screen_names",
        new=lambda: [f"s{i}" for i in range(len(words))],
    )
    mocker.patch.object(
        detector_module, "screen_landmark_rules", new=screen_landmark_rules
    )
    mocker.patch.object(
        detector_module, "screen_verify_config_fingerprint", new=lambda: ("test",)
    )
    mocker.patch.object(detector_module, "screen_verify_parent", new=lambda _s: None)
    # Force the sharded path regardless of the CI host's core count.
    mocker.patch.object(detector_module, "_landmark_worker_count", new=lambda: 4)
    detector_module.ScreenDetector._landmark_rules_cache.clear()
    detector_module.ScreenDetector._landmark_rules_cache_fp = None
    screen_graph.invalidate_screen_verify_config()

    class _CountingOcr:
        def __init__(self) -> None:
            self.calls = 0
            self.regions_per_call: list[list[Region]] = []

        async def ocr_regions(
            self,
            _image: np.ndarray,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            **_kwargs: Any,
        ) -> list[OCRResult]:
            self.calls += 1
            self.regions_per_call.append(list(regions))
            return [
                OCRResult(
                    region_id=region_ids[i] if region_ids is not None else f"r{i}",
                    text="Charlie",
                    confidence=0.99,
                )
                for i in range(len(regions))
            ]

    fake_ocr = _CountingOcr()
    detector = ScreenDetector(fake_ocr)  # type: ignore[arg-type]
    detector._area_doc = {
        "screens": [
            {
                "screen_id": "",
                "regions": [
                    {
                        "name": "page.common.title",
                        "action": "text",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 10},
                    }
                ],
            }
        ]
    }

    try:
        detected = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))
    finally:
        screen_graph.invalidate_screen_verify_config()

    # Only "Charlie" (s2) matches the OCR'd text → that screen wins.
    assert str(detected) == "s2"
    # The optimization invariant: ONE batched OCR call carrying all eight
    # same-bbox rules — not one call per shard.
    assert fake_ocr.calls == 1
    assert len(fake_ocr.regions_per_call[0]) == len(words)
