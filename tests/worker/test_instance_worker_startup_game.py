"""Startup game launch gate on ``InstanceWorkerHealthMixin``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config.loader import (
    InstanceConfig,
    OcrConfig,
    RedisConfig,
    SchedulerConfig,
    Settings,
    WorkerConfig,
)
from worker.instance_worker_health import InstanceWorkerHealthMixin


def _default_settings(*, game_foreground_timeout_seconds: int = 120) -> Settings:
    return Settings(
        redis=RedisConfig(url="redis://localhost:6379/0"),
        ocr=OcrConfig(),
        scheduler=SchedulerConfig(),
        worker=WorkerConfig(game_foreground_timeout_seconds=game_foreground_timeout_seconds),
        instances=[
            InstanceConfig(instance_id="bs1", bluestacks_window_title="127.0.0.1:5555"),
        ],
    )


class _Worker(InstanceWorkerHealthMixin):
    def __init__(self, settings: Settings | None = None) -> None:
        self._cfg = SimpleNamespace(
            instance_id="bs1",
            bluestacks_window_title="127.0.0.1:5555",
        )
        self._settings = settings or _default_settings()
        self._bot_actions = MagicMock()
        self._ui_paused = False
        self._startup_pause_reason = ""


def test_startup_pauses_when_device_offline() -> None:
    worker = _Worker()
    with (
        patch(
            "adb.AdbController.list_devices",
            return_value=["127.0.0.1:5615"],
        ),
        patch.object(worker, "_bot_actions", MagicMock()),
    ):
        ok = worker._ensure_whiteout_at_worker_start()

    assert ok is False
    assert worker._ui_paused is True
    assert worker._startup_pause_reason == "device offline (ADB)"
    worker._bot_actions.ensure_game_foreground.assert_not_called()


def test_startup_launches_until_foreground() -> None:
    worker = _Worker(_default_settings(game_foreground_timeout_seconds=60))
    worker._bot_actions = MagicMock()
    worker._bot_actions.is_game_foreground.side_effect = [False, False, True]

    with patch(
        "adb.AdbController.list_devices",
        return_value=["127.0.0.1:5555"],
    ):
        ok = worker._ensure_whiteout_at_worker_start()

    assert ok is True
    assert worker._ui_paused is False
    assert worker._bot_actions.ensure_game_foreground.call_count >= 1
    worker._bot_actions.restart_application.assert_not_called()


def test_startup_pauses_when_game_never_ready() -> None:
    worker = _Worker(_default_settings(game_foreground_timeout_seconds=1))
    worker._bot_actions = MagicMock()
    worker._bot_actions.is_game_foreground.return_value = False
    t0 = 1000.0
    mono = [t0]

    def _advance_sleep(_s: float) -> None:
        mono[0] += 5.0

    with (
        patch(
            "adb.AdbController.list_devices",
            return_value=["127.0.0.1:5555"],
        ),
        patch("worker.instance_worker_health.time.sleep", side_effect=_advance_sleep),
        patch("worker.instance_worker_health.time.monotonic", side_effect=lambda: mono[0]),
    ):
        ok = worker._ensure_whiteout_at_worker_start()

    assert ok is False
    assert worker._ui_paused is True
    assert worker._startup_pause_reason == "game not foreground at startup"
    assert worker._bot_actions.restart_application.called
