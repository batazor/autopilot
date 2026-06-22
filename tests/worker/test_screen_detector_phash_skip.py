"""Frame-unchanged skip: when the frame is visually identical to the last
detected one, the worker reuses the last screen instead of re-running the
detector (and never while navigating / past the force-detect interval)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from layout.template_match import _hamming64, _phash64
from navigation.detector import ScreenName
from worker.instance_worker import InstanceWorker


class _FakeDetector:
    def __init__(self, *, sticky: bool = True) -> None:
        self.calls = 0
        self.last_used_sticky_verify = sticky

    async def detect_screen(
        self,
        _image_bgr: np.ndarray,
        *,
        hint: object = None,
        expected: object = None,
    ) -> ScreenName:
        _ = (hint, expected)
        self.calls += 1
        return ScreenName.MAIN_CITY


class _FakeRedis:
    """Minimal async redis: serves a fixed nav_expected_screen, swallows hset."""

    def __init__(self, nav_expected: str | None = None) -> None:
        self._nav = nav_expected

    async def hget(self, _key: str, field: str) -> bytes | None:
        if field == "nav_expected_screen" and self._nav is not None:
            return self._nav.encode()
        return None

    async def hset(self, *_a: object, **_k: object) -> None:
        return None


def _frame(seed: int) -> np.ndarray:
    """A deterministic, structured BGR frame (distinct phash per seed)."""
    rng = np.random.RandomState(seed)
    base = rng.randint(0, 255, size=(64, 64, 3), dtype=np.uint8)
    # Upscale so it looks like a real 720x1280-ish frame; structure is preserved.
    return np.kron(base, np.ones((20, 11, 1), dtype=np.uint8))


def _worker(detector: _FakeDetector, redis: object | None) -> InstanceWorker:
    worker = object.__new__(InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis
    worker._screen_detector = detector
    worker._last_detected_screen = "main_city"
    worker._last_detected_screen_at = 0.0
    worker._unknown_since = 0.0
    worker._screen_unknown_streak = 0
    worker._last_detect_phash = None
    worker._last_full_detect_at = 0.0
    worker._last_detect_path = ""
    # Success path calls this; keep it inert for the unit test.
    worker._note_boot_interactive_screen = lambda _s: None
    return worker


def _arm_skip(worker: InstanceWorker, frame: np.ndarray) -> None:
    """Pretend we just detected on ``frame`` a moment ago."""
    worker._last_detect_phash = _phash64(frame)
    worker._last_full_detect_at = time.monotonic()


@pytest.mark.asyncio
async def test_skips_detection_when_frame_unchanged() -> None:
    detector = _FakeDetector()
    worker = _worker(detector, redis=None)
    frame = _frame(1)
    _arm_skip(worker, frame)

    current = await worker._detect_current_screen_on_frame(frame)

    assert current == "main_city"
    assert detector.calls == 0  # detector never invoked
    assert worker._last_detect_path == "skipped_phash"


@pytest.mark.asyncio
async def test_detects_when_frame_changed() -> None:
    detector = _FakeDetector()
    worker = _worker(detector, redis=None)
    _arm_skip(worker, _frame(1))
    other = _frame(2)
    # Precondition: the two frames really differ beyond the skip threshold.
    assert _hamming64(_phash64(_frame(1)), _phash64(other)) > 4

    await worker._detect_current_screen_on_frame(other)

    assert detector.calls == 1


@pytest.mark.asyncio
async def test_does_not_skip_while_navigating() -> None:
    detector = _FakeDetector()
    worker = _worker(detector, redis=_FakeRedis(nav_expected="mail"))
    frame = _frame(1)
    _arm_skip(worker, frame)

    await worker._detect_current_screen_on_frame(frame)

    assert detector.calls == 1  # nav in progress → always detect


@pytest.mark.asyncio
async def test_force_interval_detects_even_when_unchanged() -> None:
    detector = _FakeDetector()
    worker = _worker(detector, redis=None)
    frame = _frame(1)
    worker._last_detect_phash = _phash64(frame)
    worker._last_full_detect_at = time.monotonic() - 999.0  # stale → force detect

    await worker._detect_current_screen_on_frame(frame)

    assert detector.calls == 1


@pytest.mark.asyncio
async def test_detection_arms_the_skip_anchor() -> None:
    detector = _FakeDetector(sticky=True)
    worker = _worker(detector, redis=None)
    frame = _frame(1)
    assert worker._last_detect_phash is None

    await worker._detect_current_screen_on_frame(frame)

    assert detector.calls == 1
    assert worker._last_detect_phash == _phash64(frame)
    assert worker._last_detect_path == "sticky_hit"
    # A second identical tick now skips.
    await worker._detect_current_screen_on_frame(frame)
    assert detector.calls == 1
    assert worker._last_detect_path == "skipped_phash"
