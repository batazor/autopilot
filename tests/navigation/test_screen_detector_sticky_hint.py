"""Sticky screen detection: ``detect_screen(image, hint=<screen>)`` confirms
the hint by running ONLY that screen's rules before falling back to the
global scan.

Why: the bot dwells on one screen for many ticks (a long DSL scenario, a
chapter-task wait). The previous implementation ran ``_detect_by_text_switch``
(~100 cases) → ``_detect_by_match_landmarks`` (~11 screens × multiple
rules) → OCR landmarks every tick regardless. With sticky, a single
template-match against the hint's own landmark is usually enough — the
global scan only runs when the hint is stale (the bot navigated away).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

import navigation.screen_graph as screen_graph
from config.loader import get_settings
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient, OCRResult

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from layout.types import Region

pytestmark = pytest.mark.skip(
    reason="legacy text_switch detector coverage; rewrite for template landmarks"
)


class _FakeOcrClient:
    def __init__(self, text_by_region: dict[str, str] | None = None) -> None:
        self.text_by_region = text_by_region or {}
        self.calls: list[list[str]] = []

    async def ocr_regions(
        self,
        _image: np.ndarray,
        regions: list[Region],
        *,
        region_ids: list[str] | None = None,
    ) -> list[OCRResult]:
        rids = list(region_ids or [f"r{i}" for i in range(len(regions))])
        self.calls.append(rids)
        return [
            OCRResult(
                region_id=rid,
                text=self.text_by_region.get(rid, ""),
                confidence=0.99,
            )
            for rid in rids
        ]


@pytest.fixture
def _yaml_config(mocker, tmp_path: Path) -> Iterator[None]:
    """Two screens (arena, main_city) with disjoint OCR landmarks + a shared
    ``page_title`` text_switch. Enough to tell sticky from full pipeline."""
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
text_switch:
  - ocr: page_title
    threshold: 0.8
    cases:
      arena: [arena]
      main_city: [city]

screens:
  arena:
    landmarks:
      - ocr: arena_marker
        contains: [arena]
        threshold: 0.8
  main_city:
    landmarks:
      - ocr: city_marker
        contains: [city]
        threshold: 0.8
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    yield
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def _detector_with(text_by_region: dict[str, str]) -> ScreenDetector:
    detector = ScreenDetector(OcrClient(get_settings()))
    detector._client = _FakeOcrClient(text_by_region)  # ty: ignore[invalid-assignment]
    detector._area_doc = {
        "screens": [
            {
                "screen_id": "arena",
                "regions": [
                    {"name": "page_title", "bbox": {"x": 10, "y": 5, "width": 30, "height": 5}},
                    {"name": "arena_marker", "bbox": {"x": 10, "y": 40, "width": 30, "height": 10}},
                    {"name": "city_marker", "bbox": {"x": 60, "y": 40, "width": 30, "height": 10}},
                ],
            }
        ]
    }
    return detector


@pytest.mark.asyncio
async def test_sticky_verify_short_circuits_when_hint_still_holds(
    _yaml_config: None,
) -> None:
    """Hint=arena; frame's ``arena_marker`` OCR reads "arena".
    Sticky must return arena after ONLY checking arena's landmarks — no
    OCR on main_city's regions. ``page_title`` (text_switch) *is* scoped
    to arena's case so the verify reads it too — but it does NOT read
    city_marker, which is the global-pipeline cost we wanted to avoid."""
    detector = _detector_with({
        "arena_marker": "arena",
        "page_title": "",  # text_switch returns no text → falls through to OCR landmark
    })

    result = await detector.detect_screen(
        np.zeros((200, 100, 3), dtype=np.uint8),
        hint=ScreenName.ARENA,
    )

    assert result == ScreenName.ARENA
    assert detector.last_used_sticky_verify is True
    # The verify reads only arena's regions: page_title (because it's the
    # text_switch case for arena) and arena_marker. It must NOT touch
    # city_marker — that's the per-screen scoping win.
    fake = detector._client  # type: ignore[assignment]
    all_ocr_rids = {rid for call in fake.calls for rid in call}  # ty: ignore[unresolved-attribute]
    assert "city_marker" not in all_ocr_rids, fake.calls  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_sticky_disabled_for_main_city_hint(_yaml_config: None) -> None:
    """main_city is a hub under transient overlays (popups/modals): its
    landmarks remain visible under the overlay, so sticky-verify would
    falsely confirm main_city while the real screen is the overlay. The
    detector must always run the full pipeline for hint=main_city."""
    detector = _detector_with({
        "page_title": "arena",  # full pipeline catches the real screen
        "city_marker": "city",  # would falsely confirm main_city under sticky
        "arena_marker": "arena",
    })

    result = await detector.detect_screen(
        np.zeros((200, 100, 3), dtype=np.uint8),
        hint=ScreenName.MAIN_CITY,
    )

    assert result == ScreenName.ARENA
    assert detector.last_used_sticky_verify is False


@pytest.mark.asyncio
async def test_sticky_falls_back_to_full_pipeline_when_hint_stale(
    _yaml_config: None,
) -> None:
    """Hint=arena; frame actually shows main_city (page_title=='city',
    arena_marker reads ''). Sticky verify fails → full pipeline runs and
    correctly returns main_city. ``last_used_sticky_verify`` reflects
    that we fell back."""
    detector = _detector_with({
        "page_title": "city",
        "arena_marker": "",
        "city_marker": "city",
    })

    result = await detector.detect_screen(
        np.zeros((200, 100, 3), dtype=np.uint8),
        hint=ScreenName.ARENA,
    )

    assert result == ScreenName.MAIN_CITY
    assert detector.last_used_sticky_verify is False
    fake = detector._client  # type: ignore[assignment]
    # The verify path OCR'd arena_marker once and failed; the full pipeline
    # then OCR'd page_title (text_switch caught it as 'city' → main_city).
    assert ["arena_marker"] in fake.calls  # ty: ignore[unresolved-attribute]
    assert ["page_title"] in fake.calls  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_detect_with_no_hint_runs_full_pipeline(_yaml_config: None) -> None:
    """No hint passed → no sticky verify, current behavior preserved."""
    detector = _detector_with({"page_title": "Arena"})

    result = await detector.detect_screen(np.zeros((200, 100, 3), dtype=np.uint8))

    assert result == ScreenName.ARENA
    assert detector.last_used_sticky_verify is False
    fake = detector._client  # type: ignore[assignment]
    assert ["page_title"] in fake.calls  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_detect_with_unknown_hint_runs_full_pipeline(_yaml_config: None) -> None:
    """``hint=UNKNOWN`` is treated as "no hint" — full pipeline runs."""
    detector = _detector_with({"page_title": "Arena"})

    result = await detector.detect_screen(
        np.zeros((200, 100, 3), dtype=np.uint8),
        hint=ScreenName.UNKNOWN,
    )

    assert result == ScreenName.ARENA
    assert detector.last_used_sticky_verify is False


@pytest.mark.asyncio
async def test_detect_with_bogus_hint_string_runs_full_pipeline(
    _yaml_config: None,
) -> None:
    """A hint string that isn't a known screen must not crash — just
    skip the sticky path and run the full pipeline."""
    detector = _detector_with({"page_title": "Arena"})

    result = await detector.detect_screen(
        np.zeros((200, 100, 3), dtype=np.uint8),
        hint="some_screen_that_never_existed",
    )

    assert result == ScreenName.ARENA
    assert detector.last_used_sticky_verify is False


@pytest.mark.asyncio
async def test_sticky_text_switch_scoped_to_hint_only(
    mocker, tmp_path: Path
) -> None:
    """When hint's only rule is via text_switch (no landmark block), the
    sticky path still resolves it — and only checks the hint's own case,
    not the other ~100 cases the global text_switch would scan."""
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
text_switch:
  - ocr: page_title
    threshold: 0.8
    cases:
      arena: [arena]
      training: [training]
      vip: [vip]
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        detector = ScreenDetector(OcrClient(get_settings()))
        detector._client = _FakeOcrClient({"page_title": "Arena"})  # ty: ignore[invalid-assignment]
        detector._area_doc = {
            "screens": [
                {
                    "screen_id": "any",
                    "regions": [
                        {
                            "name": "page_title",
                            "bbox": {"x": 10, "y": 5, "width": 30, "height": 5},
                        },
                    ],
                }
            ]
        }
        result = await detector.detect_screen(
            np.zeros((200, 100, 3), dtype=np.uint8),
            hint=ScreenName.ARENA,
        )
        assert result == ScreenName.ARENA
        assert detector.last_used_sticky_verify is True
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_sticky_verify_returns_false_when_no_rules_for_hint(
    mocker, tmp_path: Path
) -> None:
    """A screen with no landmarks AND not named in any text_switch case
    can't be sticky-verified — fall through to the global pipeline."""
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
text_switch:
  - ocr: page_title
    threshold: 0.8
    cases:
      arena: [arena]

screens:
  arena:
    landmarks:
      - ocr: arena_marker
        contains: [arena]
        threshold: 0.8
  main_city: {}
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        detector = ScreenDetector(OcrClient(get_settings()))
        detector._client = _FakeOcrClient({"page_title": "Arena"})  # ty: ignore[invalid-assignment]
        detector._area_doc = {
            "screens": [
                {
                    "screen_id": "any",
                    "regions": [
                        {"name": "page_title", "bbox": {"x": 10, "y": 5, "width": 30, "height": 5}},
                        {"name": "arena_marker", "bbox": {"x": 10, "y": 40, "width": 30, "height": 10}},
                    ],
                }
            ]
        }
        # Hint main_city has no landmarks and isn't named in text_switch.
        # Sticky verify can't fire; full pipeline detects arena from page_title.
        result = await detector.detect_screen(
            np.zeros((200, 100, 3), dtype=np.uint8),
            hint=ScreenName.MAIN_CITY,
        )
        assert result == ScreenName.ARENA
        assert detector.last_used_sticky_verify is False
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_worker_passes_last_detected_screen_as_hint(
    mocker, redis_async: Any
) -> None:
    """End-to-end: the worker's screen mixin must propagate
    ``_last_detected_screen`` as ``hint`` to the detector."""
    from types import SimpleNamespace

    from worker.instance_worker import InstanceWorker

    seen_hints: list[object] = []

    class _SpyDetector:
        async def detect_screen(
            self, _image: np.ndarray, *, hint: object = None
        ) -> ScreenName:
            seen_hints.append(hint)
            return ScreenName.MAIN_CITY

    worker = object.__new__(InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis_async
    worker._screen_detector = _SpyDetector()
    worker._last_detected_screen = "main_city"
    worker._last_detected_screen_at = 0.0
    worker._unknown_since = 0.0
    worker._screen_unknown_streak = 0

    await worker._detect_current_screen_on_frame(np.zeros((10, 10, 3), dtype=np.uint8))

    assert seen_hints == ["main_city"]
