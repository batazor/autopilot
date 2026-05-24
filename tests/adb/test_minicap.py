"""Tests for the MinicapClient and helpers in src/adb/minicap.py.

No real device or network involved — subprocess + urllib are mocked.
"""
from __future__ import annotations

import struct
import subprocess
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from adb.minicap import (
    MinicapClient,
    MinicapStatus,
    _parse_wm_size,
    _recv_exact,
    get_minicap_status,
    install_minicap,
)

# ---------------------------------------------------------------------------
# status detection
# ---------------------------------------------------------------------------


def _completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_status_both_files_present() -> None:
    """ls -l returns size in the 5th column when the file exists."""
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd and "ro.product.cpu.abi" in cmd:
            return _completed(b"arm64-v8a\n")
        if "getprop" in cmd and "ro.build.version.sdk" in cmd:
            return _completed(b"33\n")
        if "ls" in cmd and cmd[-1].endswith("/minicap.so"):
            return _completed(b"-rw-r--r-- 1 shell shell 24728 2024-01-01 minicap.so\n")
        if "ls" in cmd and cmd[-1].endswith("/minicap"):
            return _completed(b"-rwxr-xr-x 1 shell shell 653640 2024-01-01 minicap\n")
        return _completed()

    with patch("adb.minicap.subprocess.run", side_effect=fake_run):
        status = get_minicap_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert status.abi == "arm64-v8a"
    assert status.sdk == "33"
    assert status.binary_installed
    assert status.library_installed
    assert status.installed
    assert status.binary_size == 653640
    assert status.library_size == 24728
    assert status.last_error is None


def test_status_missing_binary() -> None:
    """`ls` on a missing file returns nonzero + 'No such file' in stdout (toybox quirk)."""
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "ls" in cmd and cmd[-1].endswith("/minicap.so"):
            return _completed(b"-rw-r--r-- 1 shell shell 24728 2024-01-01 minicap.so\n")
        if "ls" in cmd and cmd[-1].endswith("/minicap"):
            return _completed(b"ls: /data/local/tmp/minicap: No such file or directory\n", returncode=1)
        return _completed()

    with patch("adb.minicap.subprocess.run", side_effect=fake_run):
        status = get_minicap_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert status.binary_installed is False
    assert status.library_installed is True
    assert status.installed is False


def test_status_dict_includes_installed() -> None:
    s = MinicapStatus(serial="x", binary_installed=True, library_installed=True)
    d = s.to_dict()
    assert d["installed"] is True
    assert d["serial"] == "x"


# ---------------------------------------------------------------------------
# install flow
# ---------------------------------------------------------------------------


def test_install_downloads_and_pushes(tmp_path) -> None:
    """install_minicap should download missing files and push to device."""
    downloads: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "push" in cmd or "chmod" in cmd:
            return _completed()
        if "ls" in cmd:
            return _completed(b"-rwxr-xr-x 1 shell shell 100 2024-01-01 minicap\n")
        return _completed()

    def fake_download(url: str, dest) -> None:
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"binary contents")

    with (
        patch("adb.minicap.subprocess.run", side_effect=fake_run),
        patch("adb.minicap._download", side_effect=fake_download),
        patch("adb.minicap._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_minicap("RF8RC00M8MF", "/usr/local/bin/adb")

    assert any("libs/arm64-v8a/minicap" in u for u in downloads)
    assert any("android-33/arm64-v8a/minicap.so" in u for u in downloads)
    assert status.installed


def test_install_returns_error_when_abi_missing() -> None:
    """No ABI from getprop → install bails out with a useful error."""
    def fake_run(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return _completed()  # empty stdout for everything

    with patch("adb.minicap.subprocess.run", side_effect=fake_run):
        status = install_minicap("RF8RC00M8MF", "/usr/local/bin/adb")

    assert not status.installed
    assert status.last_error is not None
    assert "ABI" in status.last_error or "abi" in status.last_error.lower()


# ---------------------------------------------------------------------------
# wm size parsing (used to size minicap's -P virtual frame)
# ---------------------------------------------------------------------------


def test_parse_wm_size_prefers_override() -> None:
    text = "Physical size: 1080x2400\nOverride size: 720x1280\n"
    assert _parse_wm_size(text) == (720, 1280)


def test_parse_wm_size_physical_only() -> None:
    text = "Physical size: 1080x2400\n"
    assert _parse_wm_size(text) == (1080, 2400)


def test_parse_wm_size_garbage() -> None:
    assert _parse_wm_size("Display power: ON\n") is None


# ---------------------------------------------------------------------------
# socket protocol — banner + size-prefixed JPEG frames
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Minimal socket-like that yields a prepared byte stream then EOF."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._pos = 0

    def recv(self, n: int) -> bytes:
        chunk = self._payload[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_recv_exact_collects_full_payload() -> None:
    sock = _FakeSocket(b"abcdef")
    assert _recv_exact(sock, 4) == b"abcd"
    assert _recv_exact(sock, 2) == b"ef"


def test_recv_exact_raises_on_short_read() -> None:
    sock = _FakeSocket(b"ab")
    with pytest.raises(ConnectionError):
        _recv_exact(sock, 4)


def test_minicap_capture_returns_cached_frame() -> None:
    """capture() returns the most recent decoded frame the reader thread put in cache."""
    client = MinicapClient(serial="x", adb_bin="/bin/true")
    fake_img = np.zeros((10, 10, 3), dtype=np.uint8)
    # Pretend we're connected: set up the bits capture() inspects.
    from adb.minicap import _CachedFrame
    client._sock = MagicMock()
    client._reader_thread = MagicMock()
    client._reader_thread.is_alive.return_value = True
    client._cache = _CachedFrame(image=fake_img, captured_at=0.0)
    client._frame_event.set()

    img, err = client.capture(timeout_s=0.01)

    assert err == ""
    assert img is fake_img


def test_minicap_capture_not_started() -> None:
    client = MinicapClient(serial="x", adb_bin="/bin/true")
    img, err = client.capture(timeout_s=0.01)
    assert img is None
    assert "not started" in err


def test_minicap_capture_timeout_returns_cached() -> None:
    """No new frame within timeout but cache has something → return cached, no error."""
    client = MinicapClient(serial="x", adb_bin="/bin/true")
    fake_img = np.ones((5, 5, 3), dtype=np.uint8)
    from adb.minicap import _CachedFrame
    client._sock = MagicMock()
    client._reader_thread = MagicMock()
    client._reader_thread.is_alive.return_value = True
    client._cache = _CachedFrame(image=fake_img, captured_at=0.0)
    # Event NOT set → wait() will time out

    img, err = client.capture(timeout_s=0.01)

    assert err == ""
    assert img is fake_img


def test_minicap_capture_timeout_no_cache() -> None:
    """Timeout with empty cache → None + descriptive error."""
    client = MinicapClient(serial="x", adb_bin="/bin/true")
    client._sock = MagicMock()
    client._reader_thread = MagicMock()
    client._reader_thread.is_alive.return_value = True

    img, err = client.capture(timeout_s=0.01)

    assert img is None
    assert "no frame" in err.lower()


def test_minicap_frame_format_size_prefix() -> None:
    """Reader-loop expects: <4-byte LE size><JPEG bytes>. Smoke-test the struct format."""
    encoded, _ = _encode_tiny_jpeg()
    payload = struct.pack("<I", len(encoded)) + encoded
    assert struct.unpack("<I", payload[:4])[0] == len(encoded)
    assert payload[4:] == encoded


def _encode_tiny_jpeg() -> tuple[bytes, np.ndarray]:
    import cv2
    img = np.full((8, 8, 3), 127, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes(), img
