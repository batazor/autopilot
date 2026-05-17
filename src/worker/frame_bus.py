"""Process-global per-instance "current frame" holder.

The rolling worker publishes each ADB-grabbed BGR frame here; every other
consumer in the worker process (DSL matchers, OCR, scenarios, hero search)
reads from this bus instead of issuing its own ADB screencap. This pins the
invariant: exactly one ADB screencap caller per worker (the rolling loop).

Concurrency: the rolling loop runs in asyncio but invokes the grab via
``_run_blocking`` (executor thread), so ``publish()`` is called from a worker
thread. ``BotActions.capture_screen_bgr`` is invoked from DSL code via
``asyncio.to_thread``, also from worker threads. Plain ``threading`` primitives
are the right fit; the holder is process-local (Streamlit UI runs in a separate
process and reads the rolling preview PNG instead).
"""
from __future__ import annotations

import threading
import time
from typing import Final

import numpy as np


class FrameBusTimeout(RuntimeError):
    """Raised when no frame becomes available within the caller's deadline."""


_LOCK: Final[threading.Lock] = threading.Lock()
_FRAMES: dict[str, tuple[float, np.ndarray]] = {}
_EVENTS: dict[str, threading.Event] = {}


def _event(instance_id: str) -> threading.Event:
    with _LOCK:
        ev = _EVENTS.get(instance_id)
        if ev is None:
            ev = threading.Event()
            _EVENTS[instance_id] = ev
        return ev


def publish(instance_id: str, frame_bgr: np.ndarray) -> None:
    """Store ``frame_bgr`` as the latest frame for ``instance_id`` and wake waiters."""
    ts = time.monotonic()
    with _LOCK:
        _FRAMES[instance_id] = (ts, frame_bgr)
        ev = _EVENTS.get(instance_id)
        if ev is None:
            ev = threading.Event()
            _EVENTS[instance_id] = ev
    # set() then clear() so any thread currently in wait() returns, while
    # subsequent waiters will block until the next publish.
    ev.set()
    ev.clear()


def latest(instance_id: str) -> tuple[float, np.ndarray] | None:
    """Return ``(monotonic_ts, frame)`` for ``instance_id`` or ``None``."""
    with _LOCK:
        return _FRAMES.get(instance_id)


def wait_for_first(instance_id: str, *, timeout: float) -> np.ndarray:
    """Return the latest frame, blocking up to ``timeout`` seconds if none yet.

    Use at the start of DSL execution / OCR / matchers — the rolling loop has
    almost always published at least once by the time the first scenario runs,
    but on a cold-start race we want to block rather than ADB-screencap.
    """
    snap = latest(instance_id)
    if snap is not None:
        return snap[1]
    return wait_for_next(instance_id, timeout=timeout)


def wait_for_next(instance_id: str, *, timeout: float) -> np.ndarray:
    """Block until the *next* publish for ``instance_id`` and return that frame.

    Use immediately after a state-changing ADB action (tap, swipe, long_tap,
    type_text). The next rolling tick fires within ``device_reference_snapshot_interval_seconds``
    (default 1 s), so a 2 s timeout comfortably covers it; longer timeouts on
    the caller side are appropriate when the action itself takes time
    (restart_application, ensure_game_foreground).
    """
    ev = _event(instance_id)
    if not ev.wait(timeout=timeout):
        raise FrameBusTimeout(
            f"frame_bus: no new frame for {instance_id!r} within {timeout:.2f}s"
        )
    snap = latest(instance_id)
    if snap is None:
        raise FrameBusTimeout(
            f"frame_bus: event fired but no frame stored for {instance_id!r}"
        )
    return snap[1]


def reset_for_test() -> None:
    """Clear all state. Test-only — production code never calls this."""
    with _LOCK:
        _FRAMES.clear()
        _EVENTS.clear()
