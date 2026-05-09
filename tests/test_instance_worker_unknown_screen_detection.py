from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from navigation.detector import ScreenName
from worker.instance_worker import InstanceWorker


class _FakeRedis:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def hset(self, key: str, field: str, value: str) -> None:
        self.writes.append((key, field, value))


class _FakeDetector:
    def __init__(self, detected: ScreenName) -> None:
        self.detected = detected
        self.calls = 0

    async def detect_screen(self, _image_bgr: np.ndarray) -> ScreenName:
        self.calls += 1
        return self.detected


def _worker(detector: _FakeDetector) -> InstanceWorker:
    worker = object.__new__(InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = _FakeRedis()
    worker._screen_detector = detector
    return worker


@pytest.mark.asyncio
async def test_overlay_tick_detects_screen_when_current_node_unknown() -> None:
    detector = _FakeDetector(ScreenName.BUILDING)
    worker = _worker(detector)

    current = await worker._detect_current_screen_if_unknown(
        np.zeros((10, 10, 3), dtype=np.uint8),
        "-",
    )

    assert current == "building"
    assert detector.calls == 1
    assert worker._redis.writes == [
        ("wos:instance:bs1:state", "current_screen", "building")
    ]


@pytest.mark.asyncio
async def test_overlay_tick_keeps_known_screen_without_detector() -> None:
    detector = _FakeDetector(ScreenName.BUILDING)
    worker = _worker(detector)

    current = await worker._detect_current_screen_if_unknown(
        np.zeros((10, 10, 3), dtype=np.uint8),
        "main_city",
    )

    assert current == "main_city"
    assert detector.calls == 0
    assert worker._redis.writes == []


@pytest.mark.asyncio
async def test_overlay_tick_keeps_unknown_when_detector_unknown() -> None:
    detector = _FakeDetector(ScreenName.UNKNOWN)
    worker = _worker(detector)

    current = await worker._detect_current_screen_if_unknown(
        np.zeros((10, 10, 3), dtype=np.uint8),
        "",
    )

    assert current == ""
    assert detector.calls == 1
    assert worker._redis.writes == []
