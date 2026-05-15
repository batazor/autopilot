"""``BotActions`` reads frames from ``worker.frame_bus`` (rolling loop publishes).

On timeout, ``capture_screen_bgr`` falls back to a direct ADB screencap so tasks
do not hang when the bus has no frames (cold start / paused rolling / ADB stall).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from adb import BotActions
from config.loader import InstanceConfig, Settings, WorkerConfig
from worker import frame_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    frame_bus.reset_for_test()
    yield
    frame_bus.reset_for_test()


@pytest.fixture()
def _fake_settings() -> Settings:
    """Minimal Settings stub so ``BotActions()`` constructs without YAML I/O."""
    from config.loader import OcrConfig, OmniparserConfig, RedisConfig, SchedulerConfig

    return Settings(
        redis=RedisConfig(url="redis://localhost:6379/0"),
        ocr=OcrConfig(url="http://localhost:8000"),
        omniparser=OmniparserConfig(),
        scheduler=SchedulerConfig(),
        worker=WorkerConfig(),
        instances=[
            InstanceConfig(instance_id="bs1", bluestacks_window_title="127.0.0.1:5555"),
        ],
    )


def _make_frame(value: int) -> np.ndarray:
    return np.full((2, 2, 3), value, dtype=np.uint8)


def test_capture_screen_bgr_returns_published_frame(_fake_settings: Settings) -> None:
    actions = BotActions(_fake_settings)

    f = _make_frame(42)
    frame_bus.publish("bs1", f)

    got = actions.capture_screen_bgr("bs1")
    assert got is f


def test_capture_screen_bgr_blocks_until_first_publish(_fake_settings: Settings) -> None:
    """Cold-start path: scenario starts before the rolling loop has produced
    its first frame. ``capture_screen_bgr`` must block, not raise — and pick
    up the frame as soon as ``publish()`` happens.
    """
    actions = BotActions(_fake_settings)

    f = _make_frame(99)

    def _publisher() -> None:
        time.sleep(0.05)
        frame_bus.publish("bs1", f)

    threading.Thread(target=_publisher, daemon=True).start()
    started = time.monotonic()
    got = actions.capture_screen_bgr("bs1")
    assert got is f
    assert time.monotonic() - started >= 0.05


def test_capture_screen_bgr_falls_back_to_adb_when_bus_stays_empty(
    _fake_settings: Settings,
) -> None:
    actions = BotActions(_fake_settings)
    actions._FIRST_FRAME_TIMEOUT_S = 0.05  # type: ignore[misc]
    want = _make_frame(7)
    with patch.object(actions, "capture_screen_bgr_adb", return_value=want) as adb_cap:
        got = actions.capture_screen_bgr("bs1")
    assert got is want
    adb_cap.assert_called_once_with("bs1")


def test_capture_screen_bgr_does_not_call_adb_when_frame_on_bus(_fake_settings: Settings) -> None:
    """When ``frame_bus`` already has a frame, do not touch ADB."""
    actions = BotActions(_fake_settings)

    frame_bus.publish("bs1", _make_frame(1))

    # Any direct ADB call would route through ``adb.screencap``. If
    # the production code ever regresses to calling it from BotActions this
    # assertion fails because the patched function is never invoked.
    with patch("adb.screencap.adb_screencap_bgr") as mocked_adb:
        actions.capture_screen_bgr("bs1")
        assert mocked_adb.call_count == 0
