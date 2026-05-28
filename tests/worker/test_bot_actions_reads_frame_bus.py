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
from layout.types import Point
from worker import frame_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    frame_bus.reset_for_test()
    yield
    frame_bus.reset_for_test()


@pytest.fixture
def _fake_settings() -> Settings:
    """Minimal Settings stub so ``BotActions()`` constructs without YAML I/O."""
    from config.loader import OcrConfig, RedisConfig, SchedulerConfig

    return Settings(
        redis=RedisConfig(url="redis://localhost:6379/0"),
        ocr=OcrConfig(),
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
    with patch.object(actions, "capture_screen_bgr_direct", return_value=want) as direct_cap:
        got = actions.capture_screen_bgr("bs1")
    assert got is want
    direct_cap.assert_called_once_with("bs1")


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


def test_direct_capture_uses_quartz_by_default(_fake_settings: Settings) -> None:
    actions = BotActions(_fake_settings)
    want = _make_frame(11)

    with (
        patch("adb.bot_actions.quartz_screencap_bgr", return_value=want) as quartz_cap,
        patch.object(actions, "capture_screen_bgr_adb") as adb_cap,
    ):
        got = actions.capture_screen_bgr_direct("bs1")

    assert got is want
    quartz_cap.assert_called_once()
    adb_cap.assert_not_called()


def test_direct_capture_respects_adb_backend(_fake_settings: Settings) -> None:
    settings = Settings(
        redis=_fake_settings.redis,
        ocr=_fake_settings.ocr,
        scheduler=_fake_settings.scheduler,
        worker=_fake_settings.worker,
        instances=[
            InstanceConfig(
                instance_id="bs1",
                bluestacks_window_title="127.0.0.1:5555",
                screenshot_backend="adb",
            ),
        ],
    )
    actions = BotActions(settings)
    want = _make_frame(12)

    with (
        patch.object(actions, "capture_screen_bgr_adb", return_value=want) as adb_cap,
        patch("adb.bot_actions.quartz_screencap_bgr") as quartz_cap,
    ):
        got = actions.capture_screen_bgr_direct("bs1")

    assert got is want
    adb_cap.assert_called_once_with("bs1")
    quartz_cap.assert_not_called()


def test_scrcpy_capture_normalizes_frame_for_analyzers(_fake_settings: Settings) -> None:
    settings = Settings(
        redis=_fake_settings.redis,
        ocr=_fake_settings.ocr,
        scheduler=_fake_settings.scheduler,
        worker=_fake_settings.worker,
        instances=[
            InstanceConfig(
                instance_id="bs1",
                bluestacks_window_title="127.0.0.1:5555",
                screenshot_backend="scrcpy",
            ),
        ],
    )
    actions = BotActions(settings)
    raw = np.zeros((1600, 720, 3), dtype=np.uint8)

    class _Client:
        def read_latest_frame_bgr(
            self,
            *,
            timeout_s: float,
            not_before_s: float | None = None,
        ) -> tuple[np.ndarray, str]:
            return raw, ""

    with patch.object(actions, "_get_scrcpy_client", return_value=_Client()):
        got = actions.capture_screen_bgr_scrcpy("bs1")

    assert got.shape == (1280, 720, 3)
    snap = frame_bus.latest_snapshot("bs1")
    assert snap is not None
    assert snap.frame_bgr.shape == (1280, 720, 3)
    assert snap.transform is not None
    assert snap.transform.source_size == (720, 1600)


def test_scrcpy_cached_capture_uses_scrcpy_direct_timeout_when_cache_empty(
    _fake_settings: Settings,
) -> None:
    settings = Settings(
        redis=_fake_settings.redis,
        ocr=_fake_settings.ocr,
        scheduler=_fake_settings.scheduler,
        worker=_fake_settings.worker,
        instances=[
            InstanceConfig(
                instance_id="bs1",
                bluestacks_window_title="127.0.0.1:5555",
                screenshot_backend="scrcpy",
            ),
        ],
    )
    actions = BotActions(settings)
    want = _make_frame(21)

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, float | None]] = []

        def read_latest_frame_bgr(
            self,
            *,
            timeout_s: float,
            not_before_s: float | None = None,
        ) -> tuple[np.ndarray, str]:
            self.calls.append({"timeout_s": timeout_s, "not_before_s": not_before_s})
            return want, ""

    client = _Client()
    with (
        patch.object(actions, "_get_scrcpy_client", return_value=client),
        patch.object(actions, "_capture_screen_bgr_scrcpy_fast") as fast,
    ):
        got = actions.capture_screen_bgr_cached("bs1", max_age_ms=1000.0)

    assert got.shape == (1280, 720, 3)
    assert int(got[0, 0, 0]) == 21
    assert client.calls == [
        {"timeout_s": actions._NEXT_FRAME_TIMEOUT_S, "not_before_s": None}
    ]
    fast.assert_not_called()


def test_scrcpy_capture_after_tap_waits_for_post_action_boundary(
    _fake_settings: Settings,
    mocker,
) -> None:
    settings = Settings(
        redis=_fake_settings.redis,
        ocr=_fake_settings.ocr,
        scheduler=_fake_settings.scheduler,
        worker=_fake_settings.worker,
        instances=[
            InstanceConfig(
                instance_id="bs1",
                bluestacks_window_title="127.0.0.1:5555",
                screenshot_backend="scrcpy",
            ),
        ],
    )
    actions = BotActions(settings)
    fake_now = [1000.0]
    mocker.patch("adb.bot_actions.time.monotonic", new=lambda: fake_now[0])
    controller = mocker.Mock()
    controller.get_screen_resolution.return_value = (720, 1280)
    controller.tap.return_value = True
    mocker.patch.object(actions, "_controller", return_value=controller)
    raw = _make_frame(31)

    class _Client:
        def __init__(self) -> None:
            self.not_before_s: float | None = None

        def read_latest_frame_bgr(
            self,
            *,
            timeout_s: float,
            not_before_s: float | None = None,
        ) -> tuple[np.ndarray, str]:
            self.not_before_s = not_before_s
            return raw, ""

    client = _Client()
    mocker.patch.object(actions, "_get_scrcpy_client", return_value=client)

    assert actions.tap("bs1", Point(10, 20))
    got = actions.capture_screen_bgr_cached("bs1")

    assert got.shape == (1280, 720, 3)
    assert int(got[0, 0, 0]) == 31
    assert client.not_before_s == pytest.approx(1000.25)
