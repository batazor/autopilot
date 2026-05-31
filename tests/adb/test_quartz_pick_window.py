"""Window-selection tests for the Quartz screenshot backend.

Regression cover for the bug where two BlueStacks instances captured the same
window: with no explicit ``quartz_window_title`` the picker silently fell
through to the "largest BlueStacks window" branch and both devices resolved to
the same window. The picker now requires an explicit title/id to disambiguate
when more than one instance is open.
"""
from __future__ import annotations

import pytest

from adb import quartz_screencap
from adb.quartz_screencap import QuartzWindow, _pick_window


def _win(window_id: int, title: str, *, owner: str = "BlueStacks", layer: int = 0,
         w: int = 538, h: int = 932) -> QuartzWindow:
    return QuartzWindow(
        window_id=window_id, owner=owner, title=title, layer=layer,
        x=0, y=0, width=w, height=h,
    )


# Two instances open at once: "BlueStacks Air 0" (bs1) and "BlueStacks Air 7".
TWO_INSTANCES = [
    _win(3781, "BlueStacks Air 0"),
    _win(3667, "BlueStacks Air 7"),
    _win(3671, "", w=1728, h=33),       # toolbar/chrome strip
    _win(3672, "", w=500, h=500),       # chrome surface
    _win(3675, "BlueStacks Air Keymap Overlay", layer=3, w=506, h=900),
]


def test_multiple_instances_without_title_fail_loudly_instead_of_colliding() -> None:
    # No explicit title + >1 instance open: must raise, not silently pick one.
    with pytest.raises(RuntimeError, match="Cannot resolve Quartz window"):
        _pick_window(TWO_INSTANCES, instance_id="bs2", quartz_window_title="")


def test_explicit_title_disambiguates() -> None:
    win = _pick_window(TWO_INSTANCES, instance_id="bs2", quartz_window_title="BlueStacks Air 7")
    assert win.window_id == 3667


def test_single_instance_resolves_without_explicit_title() -> None:
    windows = [
        _win(3667, "BlueStacks Air 7"),
        _win(3671, "", w=1728, h=33),
        _win(3675, "BlueStacks Air Keymap Overlay", layer=3, w=506, h=900),
    ]
    win = _pick_window(windows, instance_id="bs2", quartz_window_title="")
    assert win.window_id == 3667


def test_no_bluestacks_window_raises() -> None:
    windows = [_win(1, "Safari", owner="Safari", w=800, h=600)]
    with pytest.raises(RuntimeError, match="No BlueStacks Quartz window found"):
        _pick_window(windows, instance_id="bs1", quartz_window_title="")


class _Pipe:
    def __init__(self, *, close_exc: BaseException | None = None) -> None:
        self.close_exc = close_exc
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.close_exc is not None:
            raise self.close_exc


class _FakeHelper:
    def __init__(self) -> None:
        self.stdin = _Pipe(close_exc=BrokenPipeError())
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.killed = False
        self.wait_timeout: float | None = None

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float) -> None:
        self.wait_timeout = timeout


def test_restart_sck_helper_closes_broken_stdin_pipe() -> None:
    """Close the helper pipes before dropping it so BufferedWriter __del__ is quiet."""
    helper = _FakeHelper()
    old_helper = quartz_screencap._SCK_HELPER
    try:
        quartz_screencap._SCK_HELPER = helper  # type: ignore[assignment]

        quartz_screencap._restart_sck_helper_locked()
    finally:
        quartz_screencap._SCK_HELPER = old_helper

    assert quartz_screencap._SCK_HELPER is old_helper
    assert helper.killed is True
    assert helper.wait_timeout == 1.0
    assert helper.stdin.closed is True
    assert helper.stdout.closed is True
    assert helper.stderr.closed is True
