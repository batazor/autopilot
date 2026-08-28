"""Real-time device screen stream — relays the worker's scrcpy frames as MJPEG."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_redis
from api.services import remote_control, screen_stream
from api.services.instances import list_instance_ids

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["screen"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


class TapBody(BaseModel):
    """A click on the streamed image, as fractions of its width/height."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)

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


def stream_response(
    instance_id: str, request: Request, client: redis.Redis
) -> StreamingResponse:
    """Live screen as multipart/x-mixed-replace (renders natively in <img>).

    Relays the worker's rolling-preview frames (it captures fast while the viewer
    flag is set) — no second scrcpy server is started. Shared with the
    token-addressed remote-control route, which serves the same stream to a
    helper who never sees the instance id.
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


@router.get("/instances/{instance_id}/screen/stream")
async def get_screen_stream(
    instance_id: str, request: Request, client: RedisDep
) -> StreamingResponse:
    return stream_response(instance_id, request, client)


@router.get("/instances/{instance_id}/screen/status")
def get_screen_status(instance_id: str, client: RedisDep) -> dict[str, Any]:
    return screen_stream.status(client, instance_id)


@router.post("/instances/{instance_id}/screen/tap")
def post_screen_tap(
    instance_id: str, body: TapBody, client: RedisDep
) -> dict[str, Any]:
    """Tap the live screen from the dashboard — same path a helper's click takes.

    ``delivered == 0`` means no worker is subscribed; the click is dropped
    rather than queued, since a coordinate is only meaningful against the frame
    the clicker was looking at.
    """
    delivered = remote_control.publish_tap(client, instance_id, body.x, body.y)
    return {"ok": delivered > 0, "delivered": delivered}


@router.get("/instances/{instance_id}/screen/share")
def get_screen_share(instance_id: str, client: RedisDep) -> dict[str, Any]:
    """Current share link for this instance (does not mint one)."""
    token, ttl = remote_control.current_token(client, instance_id)
    return {"token": token, "path": f"/remote/{token}" if token else None, "ttl_s": ttl}


@router.post("/instances/{instance_id}/screen/share")
def post_screen_share(instance_id: str, client: RedisDep) -> dict[str, Any]:
    """Mint (or refresh) the link a helper opens to watch and tap this screen."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    token, ttl = remote_control.issue(client, instance_id)
    return {"token": token, "path": f"/remote/{token}", "ttl_s": ttl}


@router.delete("/instances/{instance_id}/screen/share")
def delete_screen_share(instance_id: str, client: RedisDep) -> dict[str, bool]:
    """Revoke every link handed out for this instance."""
    remote_control.revoke(client, instance_id)
    return {"ok": True}
