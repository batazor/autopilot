"""High-fps cross-process frame bus for the live screen stream.

The worker owns scrcpy (two servers can't coexist on one device), so the API
relays the worker's frames. The original relay went through the rolling-preview
PNG on disk — lossless ~800 KB frames + disk round-trip capped it at a few fps.

This bus carries small JPEGs straight through Redis instead:

* The worker's dedicated publish loop pulls the freshest scrcpy frame from memory
  (~30 fps cache), JPEG-encodes it, and ``publish_jpeg``-es it here while a viewer
  is attached.
* The API stream endpoint ``read_jpeg``-s the latest frame and forwards it.

Frames are binary, but the app's shared Redis clients use
``decode_responses=True`` (would corrupt JPEG bytes). So this module keeps its
own dedicated **raw** (bytes) Redis clients — a sync one for the API reader and
an async one for the worker publisher.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis
    import redis.asyncio as aioredis

FRAME_KEY_FMT = "wos:instance:{instance_id}:screen_frame"
# Frame entry self-expires so a dead worker's last frame goes stale quickly.
FRAME_TTL_S = 3


def frame_key(instance_id: str) -> str:
    return FRAME_KEY_FMT.format(instance_id=instance_id)


# --- raw (bytes) Redis clients, lazy singletons ----------------------------- #
_sync_client = None
_sync_lock = threading.Lock()
_async_client: aioredis.Redis | None = None


def _get_sync() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        with _sync_lock:
            if _sync_client is None:
                import redis

                from config.loader import get_settings

                _sync_client = redis.Redis.from_url(
                    get_settings().redis.url, decode_responses=False
                )
    return _sync_client


def _get_async() -> aioredis.Redis:
    global _async_client
    if _async_client is None:
        import redis.asyncio as aioredis_mod

        from config.loader import get_settings

        _async_client = aioredis_mod.from_url(
            get_settings().redis.url, decode_responses=False
        )
    return _async_client


# --- API side (sync read) --------------------------------------------------- #
def read_jpeg(instance_id: str) -> tuple[bytes | None, int | None]:
    """Latest published JPEG + sequence number, or (None, None)."""
    try:
        seq_b, jpeg = _get_sync().hmget(frame_key(instance_id), "seq", "jpeg")
    except Exception:
        return None, None
    seq: int | None = None
    if seq_b is not None:
        try:
            seq = int(seq_b)
        except (TypeError, ValueError):
            seq = None
    return jpeg, seq


# --- Worker side (async publish) -------------------------------------------- #
async def publish_jpeg(instance_id: str, seq: int, jpeg: bytes) -> None:
    """Publish a freshly-encoded JPEG frame for the API to relay."""
    client = _get_async()
    key = frame_key(instance_id)
    await client.hset(key, mapping={"seq": str(seq), "jpeg": jpeg})
    await client.expire(key, FRAME_TTL_S)


async def aclose() -> None:
    """Close the async publisher client (worker shutdown)."""
    global _async_client
    client = _async_client
    _async_client = None
    if client is not None:
        with contextlib.suppress(Exception):
            await client.aclose()
