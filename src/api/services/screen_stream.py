"""Real-time device screen streaming — relay the worker's scrcpy frames.

The dashboard needs a smooth live screen, not the ~1-2s polled PNG. On this
hardware **two scrcpy servers cannot coexist** on one device (a second server is
immediately ``Terminated``), so the API must NOT start its own scrcpy while the
worker holds one. Instead we relay the worker's frames:

* The API sets a short-lived ``screen_viewers`` flag while a viewer is streaming.
* The worker's rolling-snapshot loop sees the flag and captures fast (~10-12 fps
  instead of 1 fps) — reusing the existing scrcpy → rolling-preview-PNG pipeline.
* The API stream endpoint tails that rolling PNG and pushes each new frame as a
  ``multipart/x-mixed-replace`` part — native ``<img>`` video, single encoder.

This keeps one scrcpy owner (the worker) and works whenever the worker is up —
i.e. exactly when there's gameplay worth watching (the focus-mode Play button).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from dashboard.reference_preview import load_rolling_instance_preview
from worker import screen_bus

if TYPE_CHECKING:
    import redis

# Per-instance viewer flag the worker polls to switch to fast capture. Short TTL
# so the worker reverts to the slow cadence within seconds of the last viewer
# leaving; the stream endpoint refreshes it well within the window.
_VIEWER_KEY_FMT = "wos:instance:{instance_id}:screen_viewers"
VIEWER_TTL_S = 5
# A worker rewrites ``last_seen_at`` every tick; treat a heartbeat within this
# window as "the worker (and its scrcpy) is producing frames".
_WORKER_HEARTBEAT_FRESH_S = 30.0


def viewer_key(instance_id: str) -> str:
    return _VIEWER_KEY_FMT.format(instance_id=instance_id)


def mark_viewer(client: redis.Redis, instance_id: str) -> None:
    """Refresh the viewer flag so the worker keeps capturing fast."""
    client.set(viewer_key(instance_id), "1", ex=VIEWER_TTL_S)


def viewer_active(client: redis.Redis, instance_id: str) -> bool:
    try:
        return bool(client.exists(viewer_key(instance_id)))
    except Exception:
        return False


def worker_alive(client: redis.Redis, instance_id: str) -> bool:
    try:
        raw = client.hget(f"wos:instance:{instance_id}:state", "last_seen_at")
    except Exception:
        return False
    if not raw:
        return False
    try:
        last = float(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) <= _WORKER_HEARTBEAT_FRESH_S


def read_frame_jpeg(instance_id: str) -> tuple[bytes | None, int | None]:
    """Latest high-fps JPEG frame + seq from the Redis frame bus (preferred)."""
    return screen_bus.read_jpeg(instance_id)


def read_frame_png(instance_id: str) -> tuple[bytes | None, float | None]:
    """Latest rolling-preview PNG bytes + mtime (slow disk fallback)."""
    png, _rel, mtime = load_rolling_instance_preview(instance_id)
    return png, mtime


def status(client: redis.Redis, instance_id: str) -> dict[str, object]:
    jpeg, _seq = read_frame_jpeg(instance_id)
    if jpeg is not None:
        source = "scrcpy (live bus)"
        has_frame = True
        age_ms: int | None = None
    else:
        png, mtime = read_frame_png(instance_id)
        source = "rolling preview (fallback)"
        has_frame = png is not None
        age_ms = (
            max(0, int((time.time() - mtime) * 1000)) if mtime is not None else None
        )
    return {
        "instance_id": instance_id,
        "running": worker_alive(client, instance_id),
        "viewers": viewer_active(client, instance_id),
        "has_frame": has_frame,
        "last_frame_age_ms": age_ms,
        "source": source,
    }
