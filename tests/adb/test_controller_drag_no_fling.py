"""No-fling drag: motionevent chain that holds before lift-off."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from adb.controller import AdbController


def _controller(*, motionevent: bool) -> AdbController:
    ctrl = AdbController.__new__(AdbController)
    ctrl._instance_id = "bs1"
    ctrl._serial = "127.0.0.1:5555"
    ctrl._shell = MagicMock(return_value="")
    ctrl._detect_motionevent_support = MagicMock(return_value=motionevent)
    return ctrl


def test_drag_ends_with_hold_and_up_at_target() -> None:
    ctrl = _controller(motionevent=True)

    with patch("adb.controller_input.time.sleep") as sleep:
        ok = ctrl._emit_drag_no_fling(100, 200, 400, 200, 600, hold_ms=250)

    assert ok is True
    calls = [c.args for c in ctrl._shell.call_args_list]
    assert calls[0] == ("input", "motionevent", "DOWN", "100", "200")
    assert calls[-1] == ("input", "motionevent", "UP", "400", "200")
    moves = [c for c in calls if c[2] == "MOVE"]
    assert len(moves) >= 2
    assert moves[-1] == ("input", "motionevent", "MOVE", "400", "200")
    # The pre-UP hold is the whole point: zero velocity at release → no fling.
    assert sleep.call_args_list[-1].args[0] >= 0.25


def test_drag_reports_unsupported_motionevent() -> None:
    ctrl = _controller(motionevent=False)
    assert ctrl._emit_drag_no_fling(0, 0, 100, 100, 300) is False
    ctrl._shell.assert_not_called()
