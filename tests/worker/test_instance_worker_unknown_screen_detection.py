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
        self.hints_seen: list[object] = []

    async def detect_screen(
        self,
        _image_bgr: np.ndarray,
        *,
        hint: object = None,
        expected: object = None,
    ) -> ScreenName:
        del expected  # accepted for signature parity with the real detector
        self.calls += 1
        self.hints_seen.append(hint)
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
    worker._unknown_since = 0.0
    worker._screen_unknown_streak = 0
    worker._queue = None
    return worker


def _distinct_frame(index: int) -> np.ndarray:
    """A frame whose perceptual hash differs from its neighbours by index.

    ``_detect_current_screen_on_frame`` skips the detector when a frame is
    visually identical to the last *detected* frame (phash within a few bits)
    — a real screen change moves a large region and clears that threshold. The
    fake detector returns its scripted verdict regardless of pixels, so a test
    that walks the detector through MAIL → UNKNOWN transitions must feed
    genuinely different frames, exactly as a live screen change would. Reusing
    one ``np.zeros`` frame (every uniform image hashes identically) lets the
    skip reuse the first verdict forever and the streak logic never runs.
    """
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    r = (index * 13) % 56
    c = (index * 23) % 56
    img[r : r + 8, c : c + 8] = 255
    return img


@pytest.mark.asyncio
async def test_overlay_tick_writes_detected_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current == "mail"
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "mail"


@pytest.mark.asyncio
async def test_overlay_tick_overwrites_stale_known_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current == "mail"
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "mail"


@pytest.mark.asyncio
async def test_overlay_tick_clears_unknown_when_no_previous_screen(redis_async: object) -> None:
    detector = _FakeDetector(ScreenName.UNKNOWN)
    worker = _worker(detector, redis_async)

    current = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert current is None
    assert detector.calls == 1
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == ""


@pytest.mark.asyncio
async def test_overlay_tick_debounces_transient_unknown_after_known_screen(redis_async: object) -> None:
    detector = _FakeDetector([ScreenName.MAIL, ScreenName.UNKNOWN])
    worker = _worker(detector, redis_async)

    first = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )
    second = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert first == "mail"
    assert second == "mail"
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "mail"


@pytest.mark.asyncio
async def test_detect_clears_log_node_during_detect_and_restores_after(
    redis_async: object,
) -> None:
    from config import log_context

    log_context.set_log_context(node="chief_profile")
    seen_during: list[str] = []

    class _ProbingDetector:
        calls = 0

        async def detect_screen(
            self,
            _image_bgr: np.ndarray,
            *,
            hint: object = None,
            expected: object = None,
        ) -> ScreenName:
            del hint, expected
            self.calls += 1
            seen_during.append(log_context._node.get())
            return ScreenName.MAIL

    worker = _worker(_ProbingDetector(), redis_async)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    result = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert result == "mail"
    assert seen_during == [""], (
        "Detector must run with cleared `node` context — otherwise its OCR "
        "logs inherit the previous tick's screen and read as a desync."
    )
    assert log_context._node.get() == "mail"


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
            ScreenName.MAIL,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
        ]
    )
    worker = _worker(detector, redis_async)

    for i in range(4):
        current = await worker._detect_current_screen_on_frame(_distinct_frame(i))

    assert current is None
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == ""


@pytest.mark.asyncio
async def test_unknown_since_set_on_hard_clear_and_reset_on_known(
    redis_async: object,
) -> None:
    """`_unknown_since` starts the dwell timer at hard-clear and resets on known."""
    detector = _FakeDetector(
        [
            ScreenName.MAIL,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
            ScreenName.UNKNOWN,
            ScreenName.MAIL,
        ]
    )
    worker = _worker(detector, redis_async)

    # Known frame — timer stays at 0.
    await worker._detect_current_screen_on_frame(_distinct_frame(0))
    assert worker._unknown_since == 0.0

    # Soft-unknown ticks: still 0 (current_screen is sticky).
    for i in (1, 2):
        await worker._detect_current_screen_on_frame(_distinct_frame(i))
        assert worker._unknown_since == 0.0

    # Third UNKNOWN trips the streak threshold → hard-clear sets the timer.
    await worker._detect_current_screen_on_frame(_distinct_frame(3))
    assert worker._unknown_since > 0.0

    # A known detection resets the timer.
    await worker._detect_current_screen_on_frame(_distinct_frame(4))
    assert worker._unknown_since == 0.0


@pytest.mark.asyncio
async def test_dismiss_unknown_popup_enqueues_when_unknown_for_10s_and_no_matches(
    redis_async: object,
) -> None:
    """After >= 10s unknown with no global match, the fallback enqueues
    ``dismiss_unknown_popup``. A second call within the 10s lock is a no-op."""
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)
    scheduled: list[dict] = []

    class _FakeQueue:
        async def schedule(self, **kwargs):
            scheduled.append(kwargs)
            return True

    worker._queue = _FakeQueue()
    worker._unknown_since = _t.monotonic() - 11.0
    # Past onboarding (Sawmill recorded) — the dismisser is armed. See
    # ``worker.onboarding_phase.onboarding_active``: the exit signal is the
    # recorded Sawmill build (or a resolved ``active_player``), not furnace level.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={"buildings.levels.sawmill": "1"}
    )

    await worker._maybe_dismiss_unknown_popup({}, current_screen=None)
    assert len(scheduled) == 1
    assert scheduled[0]["task_type"] == "dismiss_unknown_popup"
    assert scheduled[0]["player_id"] == ""
    assert scheduled[0]["instance_id"] == "bs1"

    # Redis NX lock prevents re-enqueue within 10s.
    await worker._maybe_dismiss_unknown_popup({}, current_screen=None)
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_dismiss_unknown_popup_deferred_during_onboarding(
    redis_async: object,
) -> None:
    """While furnace < 5 (onboarding) the dismisser is not enqueued — the unknown
    screen is the tutorial and dismissing would fight the scripted flow."""
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)
    scheduled: list[dict] = []

    class _FakeQueue:
        async def schedule(self, **kwargs):
            scheduled.append(kwargs)
            return True

    worker._queue = _FakeQueue()
    worker._unknown_since = _t.monotonic() - 11.0

    # Furnace unknown (no reader yet) → deferred.
    await worker._maybe_dismiss_unknown_popup({}, current_screen=None)
    assert scheduled == []

    # Still onboarding (furnace 4) → deferred.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={"buildings.furnace.level": "4"}
    )
    await worker._maybe_dismiss_unknown_popup({}, current_screen=None)
    assert scheduled == []


@pytest.mark.asyncio
async def test_dismiss_unknown_popup_skipped_when_a_global_rule_matched(
    redis_async: object,
) -> None:
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)
    scheduled: list[dict] = []

    class _FakeQueue:
        async def schedule(self, **kwargs):
            scheduled.append(kwargs)
            return True

    worker._queue = _FakeQueue()
    worker._unknown_since = _t.monotonic() - 30.0

    overlay_results: dict[str, object] = {"some_global_rule": {"matched": True}}
    await worker._maybe_dismiss_unknown_popup(
        overlay_results, current_screen=None
    )
    assert scheduled == []


@pytest.mark.asyncio
async def test_dismiss_unknown_popup_skipped_when_screen_is_known(
    redis_async: object,
) -> None:
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)
    scheduled: list[dict] = []

    class _FakeQueue:
        async def schedule(self, **kwargs):
            scheduled.append(kwargs)
            return True

    worker._queue = _FakeQueue()
    worker._unknown_since = _t.monotonic() - 30.0

    await worker._maybe_dismiss_unknown_popup({}, current_screen="mail")
    assert scheduled == []


@pytest.mark.asyncio
async def test_dismiss_unknown_popup_skipped_below_dwell_threshold(
    redis_async: object,
) -> None:
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)
    scheduled: list[dict] = []

    class _FakeQueue:
        async def schedule(self, **kwargs):
            scheduled.append(kwargs)
            return True

    worker._queue = _FakeQueue()
    # 5s of dwell — below the 10s threshold.
    worker._unknown_since = _t.monotonic() - 5.0

    await worker._maybe_dismiss_unknown_popup({}, current_screen=None)
    assert scheduled == []


@pytest.mark.asyncio
async def test_known_after_unknown_evicts_pending_dismiss_unknown_popup(
    redis_async: object,
) -> None:
    """unknown → known transition drops the stale fallback from the queue
    and clears its NX-lock key. An already-claimed (in-flight) copy is not
    affected — ``remove_by_task_type`` only touches ZSET members."""
    import time as _t

    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)

    removed_calls: list[tuple[str, str]] = []

    class _FakeQueue:
        async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
            removed_calls.append((task_type, instance_id))
            return 1

    worker._queue = _FakeQueue()
    # Pre-seed the unknown-dwell precondition — worker was in unknown for >0s
    # and the NX-lock from a prior fallback enqueue is still alive.
    worker._unknown_since = _t.monotonic() - 20.0
    lock_key = "wos:instance:bs1:dismiss_unknown_popup_lock"
    await redis_async.set(lock_key, "1", ex=10)  # ty: ignore[unresolved-attribute]

    result = await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert result == "mail"
    assert worker._unknown_since == 0.0
    assert removed_calls == [("dismiss_unknown_popup", "bs1")]
    assert await redis_async.get(lock_key) is None  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_known_after_known_does_not_touch_queue(
    redis_async: object,
) -> None:
    """No unknown dwell → no eviction call. Keeps the steady-state hot path
    free of an extra Redis round-trip on every successful screen-detect tick."""
    detector = _FakeDetector(ScreenName.MAIL)
    worker = _worker(detector, redis_async)

    removed_calls: list[tuple[str, str]] = []

    class _FakeQueue:
        async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
            removed_calls.append((task_type, instance_id))
            return 0

    worker._queue = _FakeQueue()
    worker._unknown_since = 0.0  # never entered unknown dwell

    await worker._detect_current_screen_on_frame(
        np.zeros((10, 10, 3), dtype=np.uint8),
    )

    assert removed_calls == []
