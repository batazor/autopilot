"""Real-time device screen stream — relays the worker's scrcpy frames as MJPEG."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Annotated, Any

import redis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from api.deps import get_redis
from api.services import screen_stream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["screen"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]

_BOUNDARY = "frame"
# Endpoint poll cadence — a bit faster than the worker's ~22 fps publish so we
# forward each new frame promptly without busy-spinning.
_POLL_PERIOD_S = 0.03
# Refresh the viewer flag well within its TTL so the worker keeps publishing.
_VIEWER_REFRESH_S = 2.0


def _multipart(content_type: str, payload: bytes) -> bytes:
    return (
        f"--{_BOUNDARY}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode() + payload + b"\r\n"


def _png_to_jpeg(png: bytes) -> bytes | None:
    """Transcode a rolling-preview PNG (~800 KB) to JPEG (~40 KB) for streaming.

    The rolling frame is lossless PNG; sending it raw at ~12 fps is ~10 MB/s.
    JPEG shrinks each frame ~100× so the live view stays smooth.
    """
    import cv2
    import numpy as np

    arr = np.frombuffer(png, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return None
    return buf.tobytes()


@router.get("/instances/{instance_id}/screen/stream")
async def get_screen_stream(
    instance_id: str, request: Request, client: RedisDep
) -> StreamingResponse:
    """Live screen as multipart/x-mixed-replace (renders natively in <img>).

    Relays the worker's rolling-preview frames (it captures fast while the viewer
    flag is set) — no second scrcpy server is started.
    """

    async def body() -> AsyncIterator[bytes]:
        last_seq: int | None = None
        last_mtime: float | None = None
        last_mark = 0.0
        while True:
            if await request.is_disconnected():
                break
            now = time.monotonic()
            if now - last_mark >= _VIEWER_REFRESH_S:
                await asyncio.to_thread(screen_stream.mark_viewer, client, instance_id)
                last_mark = now

            # Preferred: high-fps JPEG from the Redis frame bus (no disk/PNG).
            jpeg, seq = await asyncio.to_thread(
                screen_stream.read_frame_jpeg, instance_id
            )
            if jpeg is not None:
                if seq != last_seq:
                    last_seq = seq
                    yield _multipart("image/jpeg", jpeg)
            else:
                # Fallback: tail the rolling-preview PNG and transcode (slow path
                # when the worker isn't publishing the live bus yet).
                png, mtime = await asyncio.to_thread(
                    screen_stream.read_frame_png, instance_id
                )
                if png is not None and mtime != last_mtime:
                    last_mtime = mtime
                    transcoded = await asyncio.to_thread(_png_to_jpeg, png)
                    if transcoded:
                        yield _multipart("image/jpeg", transcoded)
            await asyncio.sleep(_POLL_PERIOD_S)

    return StreamingResponse(
        body(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        headers={
            # no-transform stops `next start`'s gzip middleware from buffering.
            "Cache-Control": "no-cache, no-transform, private",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
        },
    )


@router.get("/instances/{instance_id}/screen/status")
def get_screen_status(instance_id: str, client: RedisDep) -> dict[str, Any]:
    return screen_stream.status(client, instance_id)
