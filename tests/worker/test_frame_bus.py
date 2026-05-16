"""Unit tests for ``worker.frame_bus`` — process-global per-instance frame holder.

Pins the contract the rolling loop and BotActions both depend on:
* ``publish()`` makes the frame visible to subsequent ``latest()``.
* ``wait_for_first()`` blocks until the first publish and then returns.
* ``wait_for_next()`` always waits for the NEXT publish (ignoring any earlier one).
* Different instance_ids are isolated.
* Timeout raises ``FrameBusTimeout``.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from worker import frame_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    frame_bus.reset_for_test()
    yield
    frame_bus.reset_for_test()


def _make_frame(value: int) -> np.ndarray:
    """Tiny BGR-ish array with a unique payload value for identity checks."""
    return np.full((2, 2, 3), value, dtype=np.uint8)


def test_publish_then_latest_returns_same_frame() -> None:
    f = _make_frame(7)
    frame_bus.publish("inst-1", f)

    snap = frame_bus.latest("inst-1")

    assert snap is not None
    ts, got = snap
    assert got is f
    assert isinstance(ts, float)


def test_latest_returns_none_before_first_publish() -> None:
    assert frame_bus.latest("inst-cold") is None


def test_wait_for_first_returns_immediately_when_frame_exists() -> None:
    f = _make_frame(11)
    frame_bus.publish("inst-1", f)

    got = frame_bus.wait_for_first("inst-1", timeout=0.1)

    assert got is f


def test_wait_for_first_blocks_then_wakes_on_publish() -> None:
    f = _make_frame(13)
    publish_after_s = 0.05

    def _publisher() -> None:
        time.sleep(publish_after_s)
        frame_bus.publish("inst-1", f)

    t = threading.Thread(target=_publisher, daemon=True)
    t.start()

    started = time.monotonic()
    got = frame_bus.wait_for_first("inst-1", timeout=1.0)
    elapsed = time.monotonic() - started

    assert got is f
    assert elapsed >= publish_after_s
    assert elapsed < 0.5  # didn't accidentally wait full timeout
    t.join(timeout=1.0)


def test_wait_for_first_times_out_when_no_publish() -> None:
    started = time.monotonic()
    with pytest.raises(frame_bus.FrameBusTimeout):
        frame_bus.wait_for_first("inst-never", timeout=0.05)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.05


def test_wait_for_next_blocks_for_subsequent_publish() -> None:
    """Even with a published frame on hand, ``wait_for_next`` must wait for a NEW one.

    This is the post-action contract: BotActions.tap waits for the rolling
    loop's *next* tick, not a stale frame that landed before the tap.
    """
    f_old = _make_frame(1)
    f_new = _make_frame(2)
    frame_bus.publish("inst-1", f_old)

    def _publisher() -> None:
        time.sleep(0.05)
        frame_bus.publish("inst-1", f_new)

    threading.Thread(target=_publisher, daemon=True).start()

    started = time.monotonic()
    got = frame_bus.wait_for_next("inst-1", timeout=1.0)
    elapsed = time.monotonic() - started

    assert got is f_new
    assert elapsed >= 0.05


def test_wait_for_next_times_out_without_publish() -> None:
    frame_bus.publish("inst-1", _make_frame(1))  # prior publish should not satisfy
    started = time.monotonic()
    with pytest.raises(frame_bus.FrameBusTimeout):
        frame_bus.wait_for_next("inst-1", timeout=0.05)
    assert time.monotonic() - started >= 0.05


def test_instances_are_isolated() -> None:
    fa = _make_frame(10)
    fb = _make_frame(20)
    frame_bus.publish("inst-a", fa)
    frame_bus.publish("inst-b", fb)

    snap_a = frame_bus.latest("inst-a")
    snap_b = frame_bus.latest("inst-b")
    assert snap_a is not None and snap_a[1] is fa
    assert snap_b is not None and snap_b[1] is fb

    # waiting on one instance must not be woken by the other instance's publish
    def _publish_other() -> None:
        time.sleep(0.02)
        frame_bus.publish("inst-a", _make_frame(11))

    threading.Thread(target=_publish_other, daemon=True).start()
    with pytest.raises(frame_bus.FrameBusTimeout):
        frame_bus.wait_for_next("inst-b", timeout=0.1)
