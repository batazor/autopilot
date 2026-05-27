"""Tests for the ScrcpyClient and helpers in src/adb/scrcpy.py.

No real device, no real network — subprocess + socket are mocked. PyAV is
imported only when ``ScrcpyClient.start()`` runs the H.264 decoder, so the
control-path and registry tests below don't depend on it.
"""
from __future__ import annotations

import socket
import struct
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from adb.scrcpy import (
    DEFAULT_PORT_BASE,
    ScrcpyClient,
    ScrcpyStatus,
    _recv_exact,
    close_all_scrcpy_clients,
    close_scrcpy_client,
    get_or_create_scrcpy_client,
    get_scrcpy_status,
    install_scrcpy,
)


def _completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Drop any registry entries leaked by previous tests."""
    close_all_scrcpy_clients()


# ---------------------------------------------------------------------------
# status / install
# ---------------------------------------------------------------------------


def test_status_jar_present() -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd and "ro.product.cpu.abi" in cmd:
            return _completed(b"arm64-v8a\n")
        if "getprop" in cmd and "ro.build.version.sdk" in cmd:
            return _completed(b"33\n")
        if "ls" in cmd and cmd[-1].endswith("/scrcpy-server.jar"):
            return _completed(b"-rw-r--r-- 1 shell shell 65536 2024-01-01 scrcpy-server.jar\n")
        return _completed()

    with patch("adb.scrcpy.subprocess.run", side_effect=fake_run):
        status = get_scrcpy_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert status.installed
    assert status.abi == "arm64-v8a"
    assert status.sdk == "33"
    assert status.jar_size == 65536


def test_status_missing_jar() -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "ls" in cmd:
            return _completed(b"ls: /data/local/tmp/scrcpy-server.jar: No such file\n", returncode=1)
        return _completed()

    with patch("adb.scrcpy.subprocess.run", side_effect=fake_run):
        status = get_scrcpy_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert not status.installed
    assert status.abi == "arm64-v8a"


def test_install_downloads_and_pushes(tmp_path) -> None:
    downloads: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "push" in cmd:
            return _completed()
        if "ls" in cmd:
            return _completed(b"-rw-r--r-- 1 shell shell 200 2024-01-01 scrcpy-server.jar\n")
        return _completed()

    def fake_download(url: str, dest) -> None:
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jar contents")

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=fake_run),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("RF8RC00M8MF", "/usr/local/bin/adb")

    # URL points at the Genymobile/scrcpy release artifact (no .jar extension upstream).
    assert any("Genymobile/scrcpy/releases/download" in u for u in downloads)
    assert any("scrcpy-server-v" in u for u in downloads)
    assert status.installed


def test_install_skips_download_when_cached(tmp_path) -> None:
    """Cached jar in ~/.cache should not re-download on subsequent installs."""
    cached_jar = tmp_path / "scrcpy-server-v3.1.jar"
    cached_jar.write_bytes(b"prebuilt jar")
    downloads: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "push" in cmd:
            return _completed()
        if "ls" in cmd:
            return _completed(b"-rw-r--r-- 1 shell shell 12 2024-01-01 scrcpy-server.jar\n")
        return _completed()

    def fake_download(url: str, _dest) -> None:
        downloads.append(url)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=fake_run),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        install_scrcpy("X", "/usr/local/bin/adb")

    assert downloads == []  # no network call


def test_status_dict_includes_installed() -> None:
    s = ScrcpyStatus(serial="x", jar_installed=True)
    d = s.to_dict()
    assert d["installed"] is True


# ---------------------------------------------------------------------------
# socket helpers
# ---------------------------------------------------------------------------


def test_recv_exact_collects_full_payload() -> None:
    sock = MagicMock()
    sock.recv.side_effect = [b"abcd", b"efgh", b"ij"]
    assert _recv_exact(sock, 10) == b"abcdefghij"


def test_recv_exact_raises_on_short_read() -> None:
    sock = MagicMock()
    sock.recv.side_effect = [b"abc", b""]
    with pytest.raises(ConnectionError):
        _recv_exact(sock, 6)


# ---------------------------------------------------------------------------
# control message format
# ---------------------------------------------------------------------------


def _make_started_client(
    codec_size: tuple[int, int] = (720, 1280),
) -> ScrcpyClient:
    """Bypass `start()`: inject just enough state to exercise the control path."""
    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    client._codec_size = codec_size
    client._control_sock = MagicMock()
    return client


def _captured_control_writes(client: ScrcpyClient) -> list[bytes]:
    assert client._control_sock is not None
    return [c.args[0] for c in client._control_sock.sendall.call_args_list]


def test_send_touch_encodes_32_byte_message() -> None:
    """Each control touch event must be exactly 32 bytes, big-endian, type=2."""
    client = _make_started_client()
    client.tap(100, 200)
    msgs = _captured_control_writes(client)
    # tap = DOWN + UP.
    assert len(msgs) == 2
    for m in msgs:
        assert len(m) == 32, f"control msg must be 32 bytes, got {len(m)}"
        assert m[0] == 0x02, "first byte must be INJECT_TOUCH_EVENT (type 2)"


def test_send_touch_packs_coords_screen_size_pressure() -> None:
    """Round-trip the 32-byte format and assert the slot values match the API."""
    client = _make_started_client(codec_size=(720, 1280))
    client._send_touch(2, 123, 456, pressure=0xFFFF, buttons=1)
    payload = _captured_control_writes(client)[0]
    msg_type, action, ptr_id, x, y, w, h, pressure, action_btn, buttons = struct.unpack(
        ">BBQiiHHHII", payload,
    )
    assert msg_type == 2  # INJECT_TOUCH_EVENT
    assert action == 2  # ACTION_MOVE
    assert ptr_id == 0
    assert (x, y) == (123, 456)
    assert (w, h) == (720, 1280)
    assert pressure == 0xFFFF
    assert action_btn == 1  # primary
    assert buttons == 1


def test_send_touch_clamps_out_of_bounds() -> None:
    """Coords beyond the codec size clamp instead of silently propagating bogus values."""
    client = _make_started_client(codec_size=(720, 1280))
    client._send_touch(0, -100, 9999, pressure=0xFFFF, buttons=1)
    payload = _captured_control_writes(client)[0]
    _, _, _, x, y, *_ = struct.unpack(">BBQiiHHHII", payload)
    assert x == 0
    assert y == 1279  # h - 1


def test_tap_emits_down_then_up_release() -> None:
    client = _make_started_client()
    with patch("adb.scrcpy.time.sleep"):
        client.tap(50, 50)
    msgs = _captured_control_writes(client)
    down, up = msgs
    assert down[1] == 0x00  # ACTION_DOWN
    assert up[1] == 0x01    # ACTION_UP
    # DOWN keeps pressure at 0xFFFF; UP must release (pressure=0, buttons=0).
    assert down[22:24] == b"\xff\xff"
    assert up[22:24] == b"\x00\x00"


def test_long_press_holds_for_duration() -> None:
    client = _make_started_client()
    with patch("adb.scrcpy.time.sleep") as mock_sleep:
        client.long_press(10, 20, duration_ms=750)
    msgs = _captured_control_writes(client)
    assert len(msgs) == 2
    assert msgs[0][1] == 0x00 and msgs[1][1] == 0x01
    # Hold uses host-side sleep of duration_ms / 1000.
    mock_sleep.assert_called_with(0.75)


def test_swipe_emits_down_moves_up() -> None:
    client = _make_started_client()
    with patch("adb.scrcpy.time.sleep"):
        client.swipe(0, 0, 720, 1280, duration_ms=320, steps=8)
    msgs = _captured_control_writes(client)
    actions = [m[1] for m in msgs]
    # 1 DOWN + 8 MOVE + 1 UP = 10 total.
    assert actions[0] == 0x00
    assert actions[-1] == 0x01
    assert actions.count(0x02) == 8


def test_send_touch_before_start_raises() -> None:
    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    with pytest.raises(RuntimeError, match="not started"):
        client._send_touch(0, 0, 0, pressure=0, buttons=0)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_returns_same_client_per_serial() -> None:
    """One scrcpy-server process per device — repeated lookups must share."""
    a = get_or_create_scrcpy_client("SAMSUNG123", "/bin/true", port=DEFAULT_PORT_BASE)
    b = get_or_create_scrcpy_client("SAMSUNG123", "/bin/true", port=DEFAULT_PORT_BASE)
    assert a is b


def test_registry_different_serials_get_different_clients() -> None:
    a = get_or_create_scrcpy_client("A", "/bin/true")
    b = get_or_create_scrcpy_client("B", "/bin/true")
    assert a is not b


def test_close_scrcpy_client_removes_from_registry() -> None:
    a = get_or_create_scrcpy_client("Z", "/bin/true")
    close_scrcpy_client("Z")
    b = get_or_create_scrcpy_client("Z", "/bin/true")
    assert a is not b


# ---------------------------------------------------------------------------
# defensive: construction is side-effect-free
# ---------------------------------------------------------------------------


def test_construction_does_not_open_sockets() -> None:
    """Defensive — if a refactor adds an unmocked connect, this catches it."""
    with patch.object(socket, "socket") as patched:
        client = ScrcpyClient(serial="x", adb_bin="/bin/true")
        assert client._video_sock is None
        assert client._control_sock is None
        patched.assert_not_called()


def test_is_alive_false_before_start() -> None:
    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    assert not client.is_alive()
