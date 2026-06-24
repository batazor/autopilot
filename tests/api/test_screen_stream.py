"""screen_stream relay model: viewer flag, worker-liveness, frame source, status.

No scrcpy/device — the API relays the worker's rolling-preview frames and only
toggles a Redis viewer flag (the worker reads it to capture fast).
"""

from __future__ import annotations

import time

import pytest

from api.services import screen_stream


@pytest.mark.integration
def test_viewer_flag_roundtrip(redis_sync) -> None:
    assert screen_stream.viewer_active(redis_sync, "bs1") is False
    screen_stream.mark_viewer(redis_sync, "bs1")
    assert screen_stream.viewer_active(redis_sync, "bs1") is True
    # TTL is set so the worker reverts to slow capture after the viewer leaves.
    ttl = redis_sync.ttl(screen_stream.viewer_key("bs1"))
    assert 0 < ttl <= screen_stream.VIEWER_TTL_S


@pytest.mark.integration
def test_worker_alive_by_heartbeat(redis_sync) -> None:
    key = "wos:instance:bs1:state"
    assert screen_stream.worker_alive(redis_sync, "bs1") is False  # no heartbeat
    redis_sync.hset(key, "last_seen_at", str(time.time()))
    assert screen_stream.worker_alive(redis_sync, "bs1") is True
    redis_sync.hset(key, "last_seen_at", str(time.time() - 10_000))
    assert screen_stream.worker_alive(redis_sync, "bs1") is False


@pytest.mark.integration
def test_status_live_bus_preferred(redis_sync, monkeypatch) -> None:
    """When the frame bus has a JPEG, status reports the live-bus source."""
    monkeypatch.setattr(screen_stream.screen_bus, "read_jpeg", lambda _iid: (b"\xff\xd8jpeg", 7))
    redis_sync.hset("wos:instance:bs1:state", "last_seen_at", str(time.time()))
    screen_stream.mark_viewer(redis_sync, "bs1")

    st = screen_stream.status(redis_sync, "bs1")
    assert st["running"] is True
    assert st["viewers"] is True
    assert st["has_frame"] is True
    assert st["source"] == "scrcpy (live bus)"


@pytest.mark.integration
def test_status_disk_fallback(redis_sync, monkeypatch) -> None:
    """No bus frame → fall back to the rolling-preview PNG."""
    monkeypatch.setattr(screen_stream.screen_bus, "read_jpeg", lambda _iid: (None, None))
    monkeypatch.setattr(
        screen_stream,
        "load_rolling_instance_preview",
        lambda _iid: (b"\x89PNG_fake", "temporal/bs1_current_state.png", time.time() - 0.2),
    )
    redis_sync.hset("wos:instance:bs1:state", "last_seen_at", str(time.time()))

    st = screen_stream.status(redis_sync, "bs1")
    assert st["running"] is True
    assert st["has_frame"] is True
    assert st["source"] == "rolling preview (fallback)"
    assert isinstance(st["last_frame_age_ms"], int)


def test_read_frame_jpeg_delegates_to_bus(monkeypatch) -> None:
    monkeypatch.setattr(screen_stream.screen_bus, "read_jpeg", lambda _iid: (b"JPG", 3))
    jpeg, seq = screen_stream.read_frame_jpeg("bs1")
    assert jpeg == b"JPG"
    assert seq == 3


def test_read_frame_png(monkeypatch) -> None:
    monkeypatch.setattr(
        screen_stream,
        "load_rolling_instance_preview",
        lambda _iid: (b"PNGDATA", "rel", 123.0),
    )
    png, mtime = screen_stream.read_frame_png("bs1")
    assert png == b"PNGDATA"
    assert mtime == 123.0
