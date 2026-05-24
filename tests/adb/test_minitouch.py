"""Tests for the MinitouchClient and helpers in src/adb/minitouch.py.

No real device or network — subprocess + socket are mocked.
"""
from __future__ import annotations

import socket
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from adb.minitouch import (
    MinitouchBanner,
    MinitouchClient,
    MinitouchStatus,
    _parse_banner,
    _parse_physical_size,
    _read_banner,
    get_minitouch_status,
    install_minitouch,
)


def _completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


# ---------------------------------------------------------------------------
# status / install
# ---------------------------------------------------------------------------


def test_status_binary_present() -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd and "ro.product.cpu.abi" in cmd:
            return _completed(b"arm64-v8a\n")
        if "getprop" in cmd and "ro.build.version.sdk" in cmd:
            return _completed(b"33\n")
        if "ls" in cmd and cmd[-1].endswith("/minitouch"):
            return _completed(b"-rwxr-xr-x 1 shell shell 34608 2024-01-01 minitouch\n")
        return _completed()

    with patch("adb.minitouch.subprocess.run", side_effect=fake_run):
        status = get_minitouch_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert status.installed
    assert status.abi == "arm64-v8a"
    assert status.sdk == "33"
    assert status.binary_size == 34608


def test_status_missing_binary() -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "ls" in cmd:
            return _completed(b"ls: /data/local/tmp/minitouch: No such file\n", returncode=1)
        return _completed()

    with patch("adb.minitouch.subprocess.run", side_effect=fake_run):
        status = get_minitouch_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert not status.installed
    assert status.abi == "arm64-v8a"


def test_install_downloads_and_pushes(tmp_path) -> None:
    downloads: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "push" in cmd or "chmod" in cmd:
            return _completed()
        if "ls" in cmd:
            return _completed(b"-rwxr-xr-x 1 shell shell 100 2024-01-01 minitouch\n")
        return _completed()

    def fake_download(url: str, dest) -> None:
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"binary contents")

    with (
        patch("adb.minitouch.subprocess.run", side_effect=fake_run),
        patch("adb.minitouch._download", side_effect=fake_download),
        patch("adb.minitouch._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_minitouch("RF8RC00M8MF", "/usr/local/bin/adb")

    assert any("arm64-v8a/bin/minitouch" in u for u in downloads)
    assert status.installed


def test_install_no_abi_bails() -> None:
    with patch("adb.minitouch.subprocess.run", side_effect=lambda *_a, **_k: _completed()):
        status = install_minitouch("X", "/usr/local/bin/adb")
    assert not status.installed
    assert status.last_error is not None


def test_status_dict_includes_installed() -> None:
    s = MinitouchStatus(serial="x", binary_installed=True)
    d = s.to_dict()
    assert d["installed"] is True


# ---------------------------------------------------------------------------
# banner + physical size parsing
# ---------------------------------------------------------------------------


def test_parse_banner_extracts_geometry() -> None:
    text = "v 1\n^ 10 4095 4095 1000\n$ 12345\n"
    banner = _parse_banner(text)
    assert banner == MinitouchBanner(
        version=1, max_contacts=10, max_x=4095, max_y=4095, max_pressure=1000, pid=12345,
    )


def test_parse_banner_missing_geometry_raises() -> None:
    with pytest.raises(RuntimeError):
        _parse_banner("v 1\n$ 999\n")


def test_parse_physical_size_skips_override() -> None:
    text = "Physical size: 1080x2400\nOverride size: 720x1280\n"
    assert _parse_physical_size(text) == (1080, 2400)


def test_parse_physical_size_garbage() -> None:
    assert _parse_physical_size("Display power: ON\n") is None


def test_read_banner_drains_until_dollar_and_three_newlines() -> None:
    payload = b"v 1\n^ 10 4095 4095 1000\n$ 999\n"
    sock = MagicMock()
    # Yield the banner across two recv calls to exercise the loop.
    sock.recv.side_effect = [payload[:10], payload[10:]]
    text = _read_banner(sock)
    assert "$" in text
    assert text.count("\n") >= 3


def test_read_banner_socket_closed_mid_stream() -> None:
    sock = MagicMock()
    sock.recv.side_effect = [b"v 1\n", b""]
    with pytest.raises(ConnectionError):
        _read_banner(sock)


# ---------------------------------------------------------------------------
# coordinate scaling
# ---------------------------------------------------------------------------


def _make_started_client(
    max_x: int = 4095, max_y: int = 4095, max_pressure: int = 1000,
    physical: tuple[int, int] = (1080, 2400),
) -> MinitouchClient:
    """Bypass `start()` and inject the bits the input methods need."""
    client = MinitouchClient(serial="x", adb_bin="/bin/true")
    client._banner = MinitouchBanner(
        version=1, max_contacts=10, max_x=max_x, max_y=max_y,
        max_pressure=max_pressure, pid=999,
    )
    client._physical_size = physical
    client._sock = MagicMock()
    return client


def test_scale_top_left() -> None:
    client = _make_started_client()
    assert client._scale(0, 0) == (0, 0)


def test_scale_bottom_right() -> None:
    client = _make_started_client()
    assert client._scale(1080, 2400) == (4095, 4095)


def test_scale_midpoint() -> None:
    client = _make_started_client()
    # 540 / 1080 * 4095 = 2047.5 → 2048; 1200 / 2400 * 4095 = 2047.5 → 2048.
    tx, ty = client._scale(540, 1200)
    assert tx == 2048
    assert ty == 2048


def test_scale_clamps_above_max() -> None:
    client = _make_started_client()
    # Pixel slightly past physical bounds → clamped to max.
    assert client._scale(99999, 99999) == (4095, 4095)


# ---------------------------------------------------------------------------
# command emission
# ---------------------------------------------------------------------------


def _captured_writes(client: MinitouchClient) -> str:
    """Concatenate everything sent through the mocked socket."""
    assert client._sock is not None
    calls = client._sock.sendall.call_args_list
    return b"".join(c.args[0] for c in calls).decode("ascii")


def test_tap_emits_down_commit_up_commit() -> None:
    client = _make_started_client()
    client.tap(540, 1200)
    text = _captured_writes(client)
    # Body: d 0 <tx> <ty> <pressure>\nc\nu 0\nc\n  with default pressure=50.
    assert text == "d 0 2048 2048 50\nc\nu 0\nc\n"


def test_long_press_uses_server_side_wait() -> None:
    client = _make_started_client()
    client.long_press(100, 200, duration_ms=750)
    text = _captured_writes(client)
    assert "w 750" in text
    assert text.startswith("d 0 ")
    assert text.endswith("u 0\nc\n")


def test_swipe_emits_down_moves_up() -> None:
    client = _make_started_client()
    client.swipe(0, 0, 1080, 2400, duration_ms=320, steps=8)
    text = _captured_writes(client)
    assert text.startswith("d 0 0 0 50\nc\n")
    # 8 m-events between down and up.
    assert text.count("\nm 0 ") == 8
    assert text.endswith("u 0\nc\n")


def test_pressure_clamp_to_max() -> None:
    client = _make_started_client(max_pressure=100)
    client.tap(100, 100, pressure=5000)
    text = _captured_writes(client)
    # The final pressure value sent should be 100, not 5000.
    assert " 100\n" in text


def test_capture_before_start_raises_on_scale() -> None:
    client = MinitouchClient(serial="x", adb_bin="/bin/true")
    with pytest.raises(RuntimeError, match="not started"):
        client._scale(10, 20)


def test_send_before_start_raises() -> None:
    client = MinitouchClient(serial="x", adb_bin="/bin/true")
    with pytest.raises(RuntimeError, match="not started"):
        client._send("d 0 0 0 50\nc\n")


# Sanity: nothing in MinitouchClient ever actually opened a real socket.
def test_real_socket_never_called() -> None:
    """Defensive — if a refactor adds an unmocked connect, this catches it."""
    with patch.object(socket, "socket") as patched:
        client = MinitouchClient(serial="x", adb_bin="/bin/true")
        # Construction alone should not connect.
        assert client._sock is None
        patched.assert_not_called()
