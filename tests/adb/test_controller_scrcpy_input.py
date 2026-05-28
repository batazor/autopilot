from __future__ import annotations

import contextlib
from datetime import timedelta
from unittest.mock import MagicMock, patch

from adb.controller import AdbController
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
