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
    SCRCPY_SERVER_VERSION,
    ScrcpyClient,
    ScrcpyStatus,
    _human_step_sleeps,
    _human_swipe_points,
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
    cached_jar = tmp_path / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
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


def test_connect_sockets_connects_control_before_video_metadata() -> None:
    """scrcpy-server v4 sends metadata only after all expected sockets connect."""
    events: list[str] = []

    class FakeSocket:
        def __init__(self, name: str, chunks: list[bytes] | None = None) -> None:
            self.name = name
            self.chunks = list(chunks or [])

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def connect(self, _addr: tuple[str, int]) -> None:
            events.append(f"{self.name}:connect")

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
    assert events.index("control:connect") < events.index("video:recv:64")


# ---------------------------------------------------------------------------
# H.264 NAL fan-out — WebSocket subscribers
# ---------------------------------------------------------------------------


def test_subscribe_video_returns_independent_queues() -> None:
    """Each subscriber owns its own queue; one consumer falling behind must
    not starve the other (the fanout drops on a full queue per subscriber)."""
    from adb.scrcpy import VideoPacket

    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    s1 = client.subscribe_video()
    s2 = client.subscribe_video()
    assert s1.queue is not s2.queue
    assert s1.desynced is not s2.desynced

    pkt = VideoPacket(pts=1, is_config=False, is_key=True, payload=b"idr")
    client._fanout_video_packet(pkt)
    assert s1.queue.get_nowait() is pkt
    assert s2.queue.get_nowait() is pkt


def test_unsubscribe_video_stops_delivery() -> None:
    from adb.scrcpy import VideoPacket

    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    sub = client.subscribe_video()
    client.unsubscribe_video(sub)
    client._fanout_video_packet(
        VideoPacket(pts=0, is_config=False, is_key=False, payload=b"x")
    )
    assert sub.queue.empty()


def test_fanout_drops_and_flags_desync_when_queue_full() -> None:
    """A wedged subscriber must not block the reader thread, but silent drops
    corrupt the H.264 reference chain (a missed IDR makes all following
    deltas undecodable). So the fanout also sets ``desynced`` so the
    consumer can drain + wait for the next keyframe instead of feeding
    out-of-sequence frames to WebCodecs."""
    from adb.scrcpy import VideoPacket

    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    sub = client.subscribe_video()
    # Fill the queue to maxsize without ever draining.
    while not sub.queue.full():
        sub.queue.put_nowait(
            VideoPacket(pts=0, is_config=False, is_key=False, payload=b"f")
        )
    assert not sub.desynced.is_set()  # baseline: no drops yet
    overflow = VideoPacket(pts=99, is_config=False, is_key=False, payload=b"drop")
    # Must not raise; the drop is silent on the reader thread.
    client._fanout_video_packet(overflow)
    # Drain one to verify the overflow was indeed dropped (queue still full of
    # the original maxsize items, not of ``overflow``).
    head = sub.queue.get_nowait()
    assert head.payload == b"f"
    # And the consumer is informed via desynced so it can resync.
    assert sub.desynced.is_set()


def test_latest_codec_config_is_none_before_any_packet() -> None:
    client = ScrcpyClient(serial="x", adb_bin="/bin/true")
    assert client.latest_codec_config() is None


def test_close_signals_subscribers_with_end_sentinel() -> None:
    """``close()`` must wake blocked subscribers immediately.

    Without this, a WebSocket consumer parked in ``queue.get(timeout=N)``
    would hang on every scrcpy shutdown until the WS idle timeout fired —
    visible to operators as a multi-second freeze.
    """
    client = ScrcpyClient(serial="close-wake", adb_bin="/bin/true")
    sub = client.subscribe_video()
    # Fake the lifecycle bits ``close()`` would otherwise touch so we don't
    # actually have to run a scrcpy server here.
    client._proc = None
    client._video_sock = None
    client._control_sock = None

    client.close()

    # The sentinel ``None`` is queued so the consumer wakes immediately.
    assert sub.queue.get_nowait() is None
    # Belt-and-braces: desynced flag is also set, so consumers that gate on
    # the event (instead of polling the queue) wake up too.
    assert sub.desynced.is_set()


def test_close_signal_evicts_oldest_when_subscriber_queue_full() -> None:
    """If a wedged consumer left its queue full at shutdown, ``close()`` must
    still deliver the sentinel — drop the oldest packet to make room rather
    than block the teardown.
    """
    from adb.scrcpy import VideoPacket

    client = ScrcpyClient(serial="close-full", adb_bin="/bin/true")
    sub = client.subscribe_video()
    while not sub.queue.full():
        sub.queue.put_nowait(
            VideoPacket(pts=0, is_config=False, is_key=False, payload=b"f")
        )
    client._proc = None
    client._video_sock = None
    client._control_sock = None

    client.close()

    # The sentinel must be the LAST item we get back — everything older was
    # already in the queue; the eviction makes one slot for ``None``.
    last: object = "<unset>"
    while not sub.queue.empty():
        last = sub.queue.get_nowait()
    assert last is None


def test_lookup_scrcpy_client_does_not_register_a_new_one() -> None:
    """``lookup_scrcpy_client`` must never create — only observe.

    The WebSocket video route relies on this: if a UI probe arrived before
    the worker had a chance to assign its instance-slot port and resolved
    adb binary, a creating-on-lookup helper would poison the registry with
    a default-port / default-adb client the worker could never replace,
    breaking scrcpy start after the first UI probe.
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
