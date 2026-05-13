from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from navigation.detector import ScreenName
from worker.instance_worker import InstanceWorker

pytestmark = pytest.mark.integration


class _FakeDetector:
    def __init__(self, detected: ScreenName | list[ScreenName]) -> None:
        self.detected = detected
        self.calls = 0

    async def detect_screen(self, _image_bgr: np.ndarray) -> ScreenName:
        self.calls += 1
        if isinstance(self.detected, list):
            return self.detected.pop(0) if self.detected else ScreenName.UNKNOWN
        return self.detected


def _worker(detector: _FakeDetector, redis_async: object) -> InstanceWorker:
    worker = object.__new__(InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis_async
    worker._screen_detector = detector
    worker._last_detected_screen = None
    worker._last_detected_screen_at = 0.0
    worker._screen_unknown_streak = 0
    return worker


@pytest.mark.asyncio
async def test_overlay_tick_writes_detected_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.BUILDING)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current == "building"
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == "building"


@pytest.mark.asyncio
async def test_overlay_tick_overwrites_stale_known_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.BUILDING)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current == "building"
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == "building"


@pytest.mark.asyncio
async def test_overlay_tick_clears_unknown_when_no_previous_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.UNKNOWN)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current is None
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == ""


@pytest.mark.asyncio
async def test_overlay_tick_debounces_transient_unknown_after_known_screen(redis_async: object) -> None:
    detector = _FakeDetector([ScreenName.BUILDING, ScreenName.UNKNOWN])
    worker = _worker(detector, redis_async)

    first = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )
    second = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert first == "building"
    assert second == "building"
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == "building"


@pytest.mark.asyncio
async def test_detect_clears_log_node_during_detect_and_restores_after(
    redis_async: object,
) -> None:
    from config import log_context

    log_context.set_log_context(node="chief_profile")
    seen_during: list[str] = []

    class _ProbingDetector:
        calls = 0

        async def detect_screen(self, _image_bgr: np.ndarray) -> ScreenName:
            self.calls += 1
            seen_during.append(log_context._node.get())
            return ScreenName.BUILDING

    worker = _worker(_ProbingDetector(), redis_async)  # type: ignore[arg-type]

    result = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert result == "building"
    assert seen_during == [""], (
        "Detector must run with cleared `node` context — otherwise its OCR "
        "logs inherit the previous tick's screen and read as a desync."
    )
    assert log_context._node.get() == "building"


@pytest.mark.asyncio
async def test_detect_clears_log_node_on_unknown(redis_async: object) -> None:
    from config import log_context

    log_context.set_log_context(node="chief_profile")
    detector = _FakeDetector(ScreenName.UNKNOWN)
    worker = _worker(detector, redis_async)

    result = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert result is None
    assert log_context._node.get() == ""


@pytest.mark.asyncio
async def test_overlay_tick_clears_after_repeated_unknown_frames(redis_async: object) -> None:
    detector = _FakeDetector(
        [
            ScreenName.BUILDING,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
        ]
    )
    worker = _worker(detector, redis_async)

    for _ in range(4):
        current = await worker._detect_current_screen_on_frame(
            np.zeros((10, 10, 3), dtype=np.uint8),
        )

    assert current is None
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == ""
