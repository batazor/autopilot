"""Stop responsiveness: device operations bail out the moment stop is set."""

import pytest

from modules.radar.device import RadarDevice, ScanStopped


def _bare_device(abort_check) -> RadarDevice:
    # __new__ skips the constructor's adb resolution — only the abort plumbing
    # is under test here, not the controller wiring.
    dev = RadarDevice.__new__(RadarDevice)
    dev.abort_check = abort_check
    return dev


def test_maybe_abort_raises_once_stop_is_requested() -> None:
    flag = {"stop": False}
    dev = _bare_device(lambda: flag["stop"])

    dev._maybe_abort()  # no stop yet — passes through

    flag["stop"] = True
    with pytest.raises(ScanStopped):
        dev._maybe_abort()


def test_maybe_abort_is_a_noop_without_a_check() -> None:
    dev = _bare_device(None)
    dev._maybe_abort()  # standalone CLI use: no stop wiring, nothing raises
