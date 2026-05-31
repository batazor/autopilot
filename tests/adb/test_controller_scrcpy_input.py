from __future__ import annotations

import contextlib
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from adb.controller import AdbController, _ShellOutcome
from layout.types import Point


def test_swipe_uses_scrcpy_backend_instead_of_adb_motionevent() -> None:
    ctrl = AdbController.__new__(AdbController)
    ctrl._instance_id = "bs1"
    ctrl._serial = "RF8RC00M8MF"
    ctrl._input_backend = "scrcpy"
    ctrl._screen_resolution = (720, 1280)
    ctrl._approval_payload_with_preview = lambda payload: payload
    ctrl._approval_execution = lambda _req_id: contextlib.nullcontext()
    ctrl._refresh_rolling_preview = MagicMock()
    ctrl._emit_swipe_straight = MagicMock()
    ctrl._dispatch_curved_swipe = MagicMock(return_value=True)

    with (
        patch("adb.controller._require_approval", return_value=(True, "req")),
        patch("adb.controller._consume_skip", return_value=False),
        patch("adb.controller.random.randint", side_effect=lambda _a, _b: 0),
        patch("adb.controller.random.uniform", return_value=1.0),
    ):
        ok = ctrl.swipe(
            Point(10, 20),
            Point(210, 420),
            timedelta(milliseconds=300),
        )

    assert ok is True
    ctrl._emit_swipe_straight.assert_called_once()
    ctrl._dispatch_curved_swipe.assert_not_called()


def _restart_controller() -> AdbController:
    ctrl = AdbController.__new__(AdbController)
    ctrl._instance_id = "bs1"
    ctrl._serial = "127.0.0.1:5555"
    ctrl._launch_package_for_game = MagicMock(return_value="com.gof.global")
    ctrl._approval_payload_with_preview = lambda payload: payload
    ctrl._approval_execution = lambda _req_id: contextlib.nullcontext()
    ctrl._refresh_rolling_preview = MagicMock()
    ctrl._shell = MagicMock(return_value="")
    ctrl._shell_full = MagicMock(return_value=_ShellOutcome(rc=1, stdout="", stderr=""))
    return ctrl


def test_restart_application_requires_approval_before_force_stop() -> None:
    ctrl = _restart_controller()

    with patch("adb.controller._require_approval", return_value=(False, None)) as approval:
        ok = ctrl.restart_application("wos")

    assert ok is False
    approval.assert_called_once()
    payload = approval.call_args.args[1]
    assert payload["type"] == "restart_application"
    assert payload["package"] == "com.gof.global"
    ctrl._shell.assert_not_called()


def test_restart_application_force_stops_only_after_approval() -> None:
    ctrl = _restart_controller()
    ctrl._shell.side_effect = [
        "",
        "com.gof.global/com.unity3d.player.MyMainPlayerActivity",
        "",
    ]

    with (
        patch("adb.controller._require_approval", return_value=(True, "req")),
        patch("adb.controller._consume_skip", return_value=False),
        patch("adb.controller.time.sleep"),
    ):
        ok = ctrl.restart_application("wos")

    assert ok is True
    assert ctrl._shell.call_args_list == [
        call("am", "force-stop", "com.gof.global"),
        call(
            "cmd",
            "package",
            "resolve-activity",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-p",
            "com.gof.global",
            "--brief",
            timeout=10.0,
        ),
        call(
            "am",
            "start",
            "-n",
            "com.gof.global/com.unity3d.player.MyMainPlayerActivity",
            timeout=10.0,
        ),
    ]


def test_ensure_game_foreground_requires_approval_before_launch() -> None:
    ctrl = _restart_controller()
    ctrl.is_game_foreground = MagicMock(return_value=False)

    with patch("adb.controller._require_approval", return_value=(False, None)) as approval:
        ok = ctrl.ensure_game_foreground("wos")

    assert ok is False
    approval.assert_called_once()
    payload = approval.call_args.args[1]
    assert payload["type"] == "ensure_game_foreground"
    assert payload["package"] == "com.gof.global"
    ctrl._shell.assert_not_called()


def test_system_back_requires_approval_before_keyevent() -> None:
    ctrl = _restart_controller()

    with patch("adb.controller._require_approval", return_value=(False, None)) as approval:
        ok = ctrl.system_back()

    assert ok is False
    approval.assert_called_once()
    payload = approval.call_args.args[1]
    assert payload["type"] == "system_back"
    assert payload["keycode"] == "KEYCODE_BACK"
    ctrl._shell.assert_not_called()


def test_system_back_keyevent_runs_only_after_approval() -> None:
    ctrl = _restart_controller()

    with (
        patch("adb.controller._require_approval", return_value=(True, "req")),
        patch("adb.controller._consume_skip", return_value=False),
    ):
        ok = ctrl.system_back()

    assert ok is True
    ctrl._shell.assert_called_once_with("input", "keyevent", "KEYCODE_BACK")
