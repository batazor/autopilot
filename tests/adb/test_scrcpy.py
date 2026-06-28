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

import numpy as np
import pytest

from adb.scrcpy import (
    _MIN_SERVER_JAR_SIZE,
    DEFAULT_PORT_BASE,
    SCRCPY_SERVER_VERSION,
    ScrcpyClient,
    ScrcpyStatus,
    _adb_forward_host,
    _CachedFrame,
    _human_step_sleeps,
    _human_swipe_points,
    _recv_exact,
    close_all_scrcpy_clients,
    close_scrcpy_client,
    get_or_create_scrcpy_client,
    get_scrcpy_status,
    install_scrcpy,
)

# A fake jar payload that passes the v4 size validation.
_FAKE_JAR = b"\x00" * _MIN_SERVER_JAR_SIZE


def _completed(
    stdout: bytes = b"",
    returncode: int = 0,
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


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


def test_status_reads_jar_size_with_wc_when_ls_format_varies() -> None:
    """Android `ls -l` output is not stable enough to be the only size source."""

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "wc" in cmd and cmd[-1].endswith("/scrcpy-server.jar"):
            return _completed(b"732226 /data/local/tmp/scrcpy-server.jar\n")
        if "ls" in cmd and cmd[-1].endswith("/scrcpy-server.jar"):
            return _completed(b"/data/local/tmp/scrcpy-server.jar\n")
        return _completed()

    with patch("adb.scrcpy.subprocess.run", side_effect=fake_run):
        status = get_scrcpy_status("RF8RC00M8MF", "/usr/local/bin/adb")

    assert status.installed
    assert status.jar_size == 732_226


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


def test_status_reports_unavailable_adb_serial() -> None:
    def fake_run(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return _completed(
            returncode=1,
            stderr=b"adb: device '127.0.0.1:5625' not found\n",
        )

    with patch("adb.scrcpy.subprocess.run", side_effect=fake_run):
        status = get_scrcpy_status("127.0.0.1:5625", "/usr/local/bin/adb")

    assert not status.installed
    assert status.jar_size is None
    assert status.last_error is not None
    assert "not found" in status.last_error


def test_install_does_not_push_when_adb_serial_unavailable(tmp_path) -> None:
    pushes: list[list[str]] = []
    downloads: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "push" in cmd:
            pushes.append(cmd)
        return _completed(
            returncode=1,
            stderr=b"adb: device '127.0.0.1:5625' not found\n",
        )

    def fake_download(url: str, _dest) -> None:
        downloads.append(url)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=fake_run),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("127.0.0.1:5625", "/usr/local/bin/adb")

    assert pushes == []
    assert downloads == []
    assert status.last_error is not None
    assert "not found" in status.last_error


def _fake_run_jar_on_device(jar_size: int):
    """ADB stub: getprop + push ok, jar on device reports ``jar_size`` bytes."""

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "getprop" in cmd:
            return _completed(b"arm64-v8a\n" if "ro.product.cpu.abi" in cmd else b"33\n")
        if "push" in cmd:
            return _completed()
        if "wc" in cmd:
            return _completed(f"{jar_size} /data/local/tmp/scrcpy-server.jar\n".encode())
        if "ls" in cmd:
            return _completed(
                f"-rw-r--r-- 1 shell shell {jar_size} 2024-01-01 scrcpy-server.jar\n".encode()
            )
        return _completed()

    return fake_run


def test_install_downloads_and_pushes(tmp_path) -> None:
    downloads: list[str] = []

    def fake_download(url: str, dest) -> None:
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_FAKE_JAR)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=_fake_run_jar_on_device(732_226)),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("RF8RC00M8MF", "/usr/local/bin/adb")

    # URL points at the Genymobile/scrcpy release artifact (no .jar extension upstream).
    assert any("Genymobile/scrcpy/releases/download" in u for u in downloads)
    assert any("scrcpy-server-v" in u for u in downloads)
    assert status.installed
    assert status.last_error is None


def test_install_skips_download_when_cached(tmp_path) -> None:
    """Cached jar in ~/.cache should not re-download on subsequent installs."""
    cached_jar = tmp_path / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
    cached_jar.write_bytes(_FAKE_JAR)
    downloads: list[str] = []

    def fake_download(url: str, _dest) -> None:
        downloads.append(url)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=_fake_run_jar_on_device(732_226)),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        install_scrcpy("X", "/usr/local/bin/adb")

    assert downloads == []  # no network call


def test_install_redownloads_poisoned_cache(tmp_path) -> None:
    """A truncated cache file (interrupted earlier download) must self-heal,
    not get pushed to the device forever."""
    cached_jar = tmp_path / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
    cached_jar.write_bytes(b"truncated")
    downloads: list[str] = []

    def fake_download(url: str, dest) -> None:
        downloads.append(url)
        dest.write_bytes(_FAKE_JAR)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=_fake_run_jar_on_device(732_226)),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("X", "/usr/local/bin/adb")

    assert len(downloads) == 1
    assert cached_jar.read_bytes() == _FAKE_JAR
    assert status.installed
    assert status.last_error is None


def test_install_rejects_truncated_download(tmp_path) -> None:
    """A download that comes back short must fail loudly and never be pushed."""
    pushes: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "push" in cmd:
            pushes.append(cmd)
        return _completed()

    def fake_download(url: str, dest) -> None:
        dest.write_bytes(b"html error page")

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=fake_run),
        patch("adb.scrcpy._download", side_effect=fake_download),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("X", "/usr/local/bin/adb")

    assert pushes == []
    assert status.last_error is not None
    assert "truncated" in status.last_error


def test_download_leaves_no_partial_file_on_error(tmp_path) -> None:
    """An interrupted download must not leave a truncated file at the cache
    path — that file would be trusted on every subsequent install."""
    from adb.scrcpy import _download

    dest = tmp_path / "scrcpy-server.jar"
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.side_effect = OSError("connection reset")

    with (
        patch("adb.scrcpy.urllib.request.urlopen", return_value=resp),
        pytest.raises(OSError, match="connection reset"),
    ):
        _download("https://example.invalid/jar", dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []  # temp file cleaned up too


def test_install_reports_undersized_jar_after_push(tmp_path) -> None:
    """If the device ends up with a too-small jar, last_error must say so
    instead of the start path failing with a bare \"install failed\"."""
    cached_jar = tmp_path / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
    cached_jar.write_bytes(_FAKE_JAR)

    with (
        patch("adb.scrcpy.subprocess.run", side_effect=_fake_run_jar_on_device(90_640)),
        patch("adb.scrcpy._DOWNLOAD_CACHE", tmp_path),
    ):
        status = install_scrcpy("X", "/usr/local/bin/adb")

    assert status.last_error is not None
    assert "after push" in status.last_error


def test_start_reinstalls_when_device_server_is_old() -> None:
    """An old v3.x server jar must not be accepted as current.

    v3.x artifacts are ~90 KiB; v4.0 is ~715 KiB. If we only check that
    /data/local/tmp/scrcpy-server.jar exists, a device upgraded from an older
    run launches an incompatible server and fails before the first frame.
    """
    client = ScrcpyClient(serial="RF8RC00M8MF", adb_bin="/usr/local/bin/adb")

    with (
        patch(
            "adb.scrcpy.get_scrcpy_status",
            return_value=ScrcpyStatus(
                serial="RF8RC00M8MF",
                jar_installed=True,
                jar_size=90_640,
            ),
        ),
        patch(
            "adb.scrcpy.install_scrcpy",
            return_value=ScrcpyStatus(
                serial="RF8RC00M8MF",
                jar_installed=True,
                jar_size=732_226,
            ),
        ) as install,
        patch("adb.scrcpy._run_adb"),
        patch("adb.scrcpy.subprocess.Popen") as popen,
        patch.object(ScrcpyClient, "_connect_sockets"),
        patch("adb.scrcpy.threading.Thread"),
        patch("adb.scrcpy.time.sleep"),
    ):
        popen.return_value.poll.return_value = None
        client.start()

    install.assert_called_once_with("RF8RC00M8MF", "/usr/local/bin/adb")


def test_start_does_not_install_when_adb_serial_unavailable() -> None:
    client = ScrcpyClient(serial="127.0.0.1:5625", adb_bin="/usr/local/bin/adb")

    with (
        patch(
            "adb.scrcpy.get_scrcpy_status",
            return_value=ScrcpyStatus(
                serial="127.0.0.1:5625",
                last_error="adb exited 1: adb: device '127.0.0.1:5625' not found",
            ),
        ),
        patch("adb.scrcpy.install_scrcpy") as install,
        pytest.raises(RuntimeError, match="not found"),
    ):
        client.start()

    install.assert_not_called()


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
    with patch("adb.scrcpy.random.random", return_value=0.99):
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
    with (
        patch("adb.scrcpy.time.sleep"),
        patch("adb.scrcpy.random.random", return_value=0.99),
    ):
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
    with (
        patch("adb.scrcpy.time.sleep"),
        patch("adb.scrcpy.random.random", return_value=0.99),
    ):
        client.swipe(0, 0, 720, 1280, duration_ms=320, steps=8)
    msgs = _captured_control_writes(client)
    actions = [m[1] for m in msgs]
    # 1 DOWN + 8 MOVE + 1 UP = 10 total.
    assert actions[0] == 0x00
    assert actions[-1] == 0x01
    assert actions.count(0x02) == 8
    _, _, _, x, y, *_ = struct.unpack(">BBQiiHHHII", msgs[-2])
    assert (x, y) == (719, 1279)


def test_human_swipe_points_include_exact_endpoint() -> None:
    pts = _human_swipe_points(10, 20, 110, 220, steps=12)
    assert len(pts) == 12
    assert pts[-1] == (110, 220)


def test_human_step_sleeps_preserve_duration() -> None:
    sleeps = _human_step_sleeps(900, steps=18)
    assert len(sleeps) == 18
    assert sum(sleeps) == pytest.approx(0.9)
    assert all(s > 0 for s in sleeps)


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


def test_read_latest_frame_bgr_returns_cached_frame_without_boundary() -> None:
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    frame = np.full((2, 2, 3), 7, dtype=np.uint8)
    client._cache = _CachedFrame(image=frame, captured_at=10.0)

    with patch.object(client, "is_alive", return_value=True):
        got, err = client.read_latest_frame_bgr(timeout_s=0.0)

    assert got is frame
    assert err == ""


def test_read_latest_frame_bgr_rejects_cached_frame_before_boundary() -> None:
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    frame = np.full((2, 2, 3), 7, dtype=np.uint8)
    client._cache = _CachedFrame(image=frame, captured_at=10.0)

    with patch.object(client, "is_alive", return_value=True):
        got, err = client.read_latest_frame_bgr(timeout_s=0.0, not_before_s=10.1)

    assert got is None
    assert err == "no frame received after post-action boundary"


def test_start_creates_adb_forward_before_launching_server() -> None:
    """scrcpy-server v4 expects the adb forward to exist before app_process starts."""
    events: list[str] = []
    client = ScrcpyClient(serial="S", adb_bin="/adb", port=1919)

    def fake_run_adb(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if args[:1] == ["forward"] and "--remove" not in args:
            events.append("forward:add")
        return _completed()

    def fake_popen(*_args: object, **_kwargs: object) -> MagicMock:
        events.append("popen")
        proc = MagicMock()
        proc.poll.return_value = None
        return proc

    with (
        patch(
            "adb.scrcpy.get_scrcpy_status",
            return_value=ScrcpyStatus(serial="S", jar_installed=True, jar_size=732_226),
        ),
        patch("adb.scrcpy._run_adb", side_effect=fake_run_adb),
        patch("adb.scrcpy.subprocess.Popen", side_effect=fake_popen),
        patch.object(ScrcpyClient, "_connect_sockets"),
        patch("adb.scrcpy.threading.Thread"),
        patch("adb.scrcpy.time.sleep"),
    ):
        client.start()

    assert events == ["forward:add", "popen"]


def test_start_passes_keep_active_to_server() -> None:
    client = ScrcpyClient(serial="S", adb_bin="/adb", port=1919)

    with (
        patch(
            "adb.scrcpy.get_scrcpy_status",
            return_value=ScrcpyStatus(serial="S", jar_installed=True, jar_size=732_226),
        ),
        patch("adb.scrcpy._run_adb"),
        patch("adb.scrcpy.subprocess.Popen") as popen,
        patch.object(ScrcpyClient, "_connect_sockets"),
        patch("adb.scrcpy.threading.Thread"),
        patch("adb.scrcpy.time.sleep"),
    ):
        popen.return_value.poll.return_value = None
        client.start()

    cmd = popen.call_args.args[0]
    assert "keep_active=true" in cmd
    assert "cleanup=false" in cmd


def test_adb_forward_host_defaults_to_adb_server_socket_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WOS_ADB_FORWARD_HOST", raising=False)
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:host.docker.internal:5037")

    assert _adb_forward_host() == "host.docker.internal"


def test_adb_forward_host_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:host.docker.internal:5037")
    monkeypatch.setenv("WOS_ADB_FORWARD_HOST", "adb-gateway")

    assert _adb_forward_host() == "adb-gateway"


def test_connect_sockets_connects_control_before_video_metadata() -> None:
    """scrcpy-server v4 sends metadata only after all expected sockets connect."""
    events: list[str] = []

    class FakeSocket:
        def __init__(self, name: str, chunks: list[bytes] | None = None) -> None:
            self.name = name
            self.chunks = list(chunks or [])

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def connect(self, addr: tuple[str, int]) -> None:
            events.append(f"{self.name}:connect:{addr[0]}:{addr[1]}")

        def recv(self, n: int) -> bytes:
            events.append(f"{self.name}:recv:{n}")
            if not self.chunks:
                return b""
            chunk = self.chunks.pop(0)
            if len(chunk) <= n:
                return chunk
            self.chunks.insert(0, chunk[n:])
            return chunk[:n]

    video = FakeSocket(
        "video",
        [
            b"\x00",
            b"SM-G780G" + (b"\x00" * 56),
            b"h264",
            b"\x80\x00\x00\x00" + struct.pack(">II", 1080, 2400),
        ],
    )
    control = FakeSocket("control")
    client = ScrcpyClient(serial="S", adb_bin="/adb", port=1919)

    with patch("adb.scrcpy.socket.socket", side_effect=[video, control]):
        client._connect_sockets()

    assert client.device_name == "SM-G780G"
    assert client.codec_size == (1080, 2400)
    assert events.index("control:connect:127.0.0.1:1919") < events.index(
        "video:recv:64"
    )


def test_connect_sockets_uses_forward_host_from_adb_server_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, tuple[str, int]]] = []

    class FakeSocket:
        def __init__(self, name: str, chunks: list[bytes] | None = None) -> None:
            self.name = name
            self.chunks = list(chunks or [])

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def connect(self, addr: tuple[str, int]) -> None:
            events.append((self.name, addr))

        def recv(self, n: int) -> bytes:
            if not self.chunks:
                return b""
            chunk = self.chunks.pop(0)
            if len(chunk) <= n:
                return chunk
            self.chunks.insert(0, chunk[n:])
            return chunk[:n]

    monkeypatch.delenv("WOS_ADB_FORWARD_HOST", raising=False)
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:host.docker.internal:5037")
    video = FakeSocket(
        "video",
        [
            b"\x00",
            b"SM-G780G" + (b"\x00" * 56),
            b"h264",
            b"\x80\x00\x00\x00" + struct.pack(">II", 1080, 2400),
        ],
    )
    control = FakeSocket("control")
    client = ScrcpyClient(serial="S", adb_bin="/adb", port=1919)

    with patch("adb.scrcpy.socket.socket", side_effect=[video, control]):
        client._connect_sockets()

    assert events == [
        ("video", ("host.docker.internal", 1919)),
        ("control", ("host.docker.internal", 1919)),
    ]


# ---------------------------------------------------------------------------
# video-socket auto-reconnect
# ---------------------------------------------------------------------------


def _alive_thread() -> MagicMock:
    t = MagicMock()
    t.is_alive.return_value = True
    return t


def test_read_loop_reconnects_after_drop() -> None:
    """A dropped video socket relaunches the session in place rather than
    ending the reader thread."""
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    with (
        patch.object(client, "_stream_video", side_effect=["dropped", "stopped"]),
        patch.object(client, "_restart_session", return_value=True) as restart,
    ):
        client._read_loop()
    restart.assert_called_once()


def test_read_loop_exits_when_reconnect_gives_up() -> None:
    """If every reconnect attempt fails, the reader exits so the capture path
    can recreate a fresh client."""
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    with (
        patch.object(client, "_stream_video", return_value="dropped"),
        patch.object(client, "_restart_session", return_value=False) as restart,
    ):
        client._read_loop()
    restart.assert_called_once()


def test_restart_session_relaunches_in_place() -> None:
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    with (
        patch.object(client, "_close_session") as close_session,
        patch.object(client, "_launch_session") as launch,
    ):
        ok = client._restart_session()
    assert ok is True
    close_session.assert_called_once()
    launch.assert_called_once()
    # The reconnecting flag must be cleared once the stream is back.
    assert not client._reconnecting.is_set()


def test_restart_session_retries_then_gives_up() -> None:
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    with (
        patch.object(client, "_close_session"),
        patch.object(client, "_launch_session", side_effect=OSError("boom")) as launch,
        patch.object(client._stop, "wait", return_value=False),  # no real backoff sleep
    ):
        ok = client._restart_session()
    assert ok is False
    from adb.scrcpy import _RECONNECT_MAX_ATTEMPTS

    assert launch.call_count == _RECONNECT_MAX_ATTEMPTS
    assert not client._reconnecting.is_set()


def test_is_alive_true_during_reconnect_with_sockets_down() -> None:
    """Mid-relaunch the sockets are None but the reader is up — the client must
    still read as alive so the capture path doesn't tear it down."""
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    client._reader_thread = _alive_thread()
    client._video_sock = None
    client._control_sock = None
    client._reconnecting.set()
    assert client.is_alive()


def test_read_latest_frame_serves_cached_during_reconnect_past_boundary() -> None:
    """While reconnecting, the cached frame is served even past a not_before_s
    boundary (no fresh frames arrive during a relaunch)."""
    client = ScrcpyClient(serial="s", adb_bin="/bin/true")
    client._reader_thread = _alive_thread()
    client._reconnecting.set()
    frame = np.full((2, 2, 3), 5, dtype=np.uint8)
    client._cache = _CachedFrame(image=frame, captured_at=10.0)

    got, err = client.read_latest_frame_bgr(timeout_s=0.0, not_before_s=99.0)

    assert got is frame
    assert err == ""


def test_lookup_scrcpy_client_does_not_register_a_new_one() -> None:
    """``lookup_scrcpy_client`` must never create — only observe.

    If a probe arrived before the worker had a chance to assign its
    instance-slot port and resolved adb binary, a creating-on-lookup helper
    would poison the registry with a default-port / default-adb client the
    worker could never replace, breaking scrcpy start after the first probe.
    """
    from adb.scrcpy import (
        close_scrcpy_client,
        get_or_create_scrcpy_client,
        lookup_scrcpy_client,
    )

    serial = "lookup-test"
    close_scrcpy_client(serial)  # ensure clean slate
    assert lookup_scrcpy_client(serial) is None

    created = get_or_create_scrcpy_client(serial, "/bin/true")
    assert lookup_scrcpy_client(serial) is created

    close_scrcpy_client(serial)
    assert lookup_scrcpy_client(serial) is None


def test_set_max_fps_updates_cap_and_noops_when_unchanged() -> None:
    """``set_max_fps`` updates the launch cap; unchanged value is a no-op.

    Before ``start()`` there is no video socket, so the call just records the
    new cap (the next ``_launch_session`` appends ``max_fps=N`` to server_args).
    """
    client = ScrcpyClient(serial="fps-test", adb_bin="/bin/true")
    assert client.max_fps == 0  # default: uncapped, no regression

    client.set_max_fps(8)
    assert client.max_fps == 8

    client.set_max_fps(8)  # unchanged → no-op (no exception without a socket)
    assert client.max_fps == 8

    client.set_max_fps(0)  # back to uncapped
    assert client.max_fps == 0

    client.set_max_fps(-5)  # clamped to 0
    assert client.max_fps == 0
