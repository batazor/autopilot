"""Worker passes ``nav_expected_screen`` from Redis into screen detection."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from navigation.detector import ScreenName
from worker.instance_worker import InstanceWorker


class _FakeDetector:
    def __init__(self) -> None:
        self.expected_seen: list[str | None] = []

    async def detect_screen(
        self,
        _image_bgr: np.ndarray,
        *,
        hint: object = None,
        expected: object = None,
    ) -> ScreenName:
        _ = hint
        self.expected_seen.append(
            str(expected) if expected is not None else None
        )
        return ScreenName.MAIN_CITY


def _worker(detector: _FakeDetector, redis_async: object) -> InstanceWorker:
    worker = object.__new__(InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis_async
    worker._screen_detector = detector
    worker._last_detected_screen = None
    worker._last_detected_screen_at = 0.0
    worker._unknown_since = 0.0
    worker._screen_unknown_streak = 0
    return worker


@pytest.mark.asyncio
async def test_detect_passes_nav_expected_from_redis(redis_async: object) -> None:
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"nav_expected_screen": "mail"},
    )
    detector = _FakeDetector()
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current == "main_city"
    assert detector.expected_seen == ["mail"]
