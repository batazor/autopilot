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
_VERSIONS: dict[str, int] = {}
_CONDITIONS: dict[str, threading.Condition] = {}


def _condition(instance_id: str) -> threading.Condition:
    with _LOCK:
        cond = _CONDITIONS.get(instance_id)
        if cond is None:
            cond = threading.Condition()
            _CONDITIONS[instance_id] = cond
        return cond


def publish(instance_id: str, frame_bgr: np.ndarray) -> None:
    """Store ``frame_bgr`` as the latest frame for ``instance_id`` and wake waiters."""
    ts = time.monotonic()
    cond = _condition(instance_id)
    with cond:
        with _LOCK:
            _FRAMES[instance_id] = (ts, frame_bgr)
            _VERSIONS[instance_id] = _VERSIONS.get(instance_id, 0) + 1
        cond.notify_all()


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

    Uses a monotonic version counter under a Condition so back-to-back publishes
    can't be lost between two waiter wakeups (the previous Event.set()+clear()
    pattern had a CPython race where ``clear`` could fire before a woken waiter
    re-checked ``is_set``, causing it to block again indefinitely).
    """
    cond = _condition(instance_id)
    deadline = time.monotonic() + timeout
    with cond:
        with _LOCK:
            baseline = _VERSIONS.get(instance_id, 0)
        while True:
            with _LOCK:
                if _VERSIONS.get(instance_id, 0) > baseline:
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not cond.wait(timeout=remaining):
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
        _VERSIONS.clear()
        _CONDITIONS.clear()
