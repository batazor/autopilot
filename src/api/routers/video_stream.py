"""Live H.264 video stream for the approvals UI (WebCodecs over WebSocket).

scrcpy's reader thread fans out Annex-B NAL packets to subscribers (see
``adb.scrcpy.ScrcpyClient.subscribe_video``). This router exposes each
subscriber over a WebSocket so the browser can pipe the bytes straight into
``VideoDecoder`` (WebCodecs API) — no MP4 remuxing, no server-side encode.

Wire format:
    1. Text frame (one): JSON handshake with codec string + resolution.
    2. Binary frame (one): the cached SPS+PPS config packet so the decoder
       initialises before the first delta arrives.
    3. Binary frames (streaming): per-NAL packets, started at the next keyframe
       so deltas always reference a key the client has seen.

Binary frame layout: ``flags:u8 | pts:u64-BE | payload:bytes`` where
``flags`` is a bitmask (``0x01`` = config / SPS+PPS, ``0x02`` = keyframe) and
``pts`` is scrcpy's v4 61-bit PTS in microseconds (top bits already stripped).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import struct
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from adb.scrcpy import VideoPacket, VideoSubscription, lookup_scrcpy_client
from config.loader import load_settings

logger = logging.getLogger(__name__)
router = APIRouter()

_FLAG_CONFIG = 0x01
_FLAG_KEY = 0x02
# Close the stream if scrcpy stops emitting NALs for this long. Healthy
# streams produce one packet every ~33ms, so 5s is well beyond any normal
# stall — the client will reconnect and resync from the next keyframe.
_NAL_IDLE_TIMEOUT_S = 5.0


def _find_instance_serial(instance_id: str) -> str | None:
    for inst in load_settings().instances:
        if inst.instance_id == instance_id:
            return inst.bluestacks_window_title
    return None


def _codec_string_from_config(config: bytes) -> str | None:
    """Extract the ``avc1.PPCCLL`` codec string from a config packet (SPS+PPS).

    WebCodecs ``VideoDecoder.configure`` requires this so the browser picks
    a compatible H.264 decoder up-front. The SPS NAL's first three bytes after
    the type byte are ``profile_idc``, ``constraint_set_flags``, ``level_idc``
    — exactly the three octets that go into the codec string.
    """
    start_code = b"\x00\x00\x00\x01"
    pos = config.find(start_code)
    if pos < 0:
        return None
    nal = config[pos + len(start_code) :]
    if len(nal) < 4:
        return None
    if (nal[0] & 0x1F) != 7:  # not an SPS NAL — config malformed
        return None
    return f"avc1.{nal[1]:02X}{nal[2]:02X}{nal[3]:02X}"


def _pack(pkt: VideoPacket) -> bytes:
    flags = 0
    if pkt.is_config:
        flags |= _FLAG_CONFIG
    if pkt.is_key:
        flags |= _FLAG_KEY
    return struct.pack(">BQ", flags, pkt.pts) + pkt.payload


async def _next_packet(sub: VideoSubscription) -> VideoPacket | None:
    """Pull from the bounded queue without holding the asyncio loop.

    ``queue.Queue.get`` is sync and blocking; running it in the default
    threadpool keeps the WebSocket event loop responsive to disconnects and
    other concurrent connections. Returns ``None`` when no packet arrives
    within :data:`_NAL_IDLE_TIMEOUT_S` so the caller can decide whether to
    close the stream.
    """
    try:
        return await asyncio.to_thread(sub.queue.get, True, _NAL_IDLE_TIMEOUT_S)
    except queue.Empty:
        return None


def _drain_subscription(sub: VideoSubscription) -> int:
    """Empty the subscriber's queue; returns the number of dropped packets.

    Used after the reader sets ``desynced`` so we don't forward stale deltas
    that point at an already-evicted IDR. The follow-up sync loop then waits
    for the next keyframe before resuming decoder feeds.
    """
    dropped = 0
    while True:
        try:
            sub.queue.get_nowait()
            dropped += 1
        except queue.Empty:
            return dropped


@router.websocket("/api/instances/{instance_id}/stream.h264.ws")
async def stream_h264_ws(websocket: WebSocket, instance_id: str) -> None:
    await websocket.accept()

    serial = _find_instance_serial(instance_id)
    if serial is None:
        await websocket.close(code=4404, reason="unknown instance")
        return

    # Look up only — never create. The worker owns ScrcpyClient lifecycle
    # (jar push, forward port, server start) and the registry is keyed by
    # serial; creating a client from this UI route would poison the registry
    # with a default-port / default-adb instance that the worker can never
    # replace, breaking scrcpy start after the first UI probe.
    client = lookup_scrcpy_client(serial)
    if client is None or not client.is_alive():
        await websocket.close(
            code=4503, reason="scrcpy not running — start the bot first"
        )
        return

    config = client.latest_codec_config()
    if config is None:
        await websocket.close(
            code=4503, reason="scrcpy starting — codec config not received yet"
        )
        return

    codec = _codec_string_from_config(config) or "avc1.42E029"
    width, height = client.codec_size or (0, 0)
    handshake: dict[str, Any] = {
        "codec": codec,
        "width": width,
        "height": height,
    }

    sub = client.subscribe_video()
    try:
        await websocket.send_text(json.dumps(handshake))
        # Send the cached config so the decoder can be configured before any
        # delta arrives. Without this a subscriber joining mid-stream would
        # silently drop frames until the next config packet (which scrcpy
        # doesn't re-emit on its own).
        await websocket.send_bytes(
            _pack(
                VideoPacket(
                    pts=0,
                    is_config=True,
                    is_key=False,
                    payload=config,
                )
            )
        )

        synced = False
        while True:
            if sub.desynced.is_set():
                # Reader thread had to drop at least one packet for this
                # subscriber (slow WS / browser stall). Anything still queued
                # may reference an evicted IDR, so flush it and wait for the
                # next keyframe before resuming decoder feeds — otherwise
                # WebCodecs errors on undecodable deltas. The cached config
                # is still on the client side, so we don't need to resend it.
                sub.desynced.clear()
                dropped = _drain_subscription(sub)
                logger.info(
                    "h264 stream %s: consumer behind — dropped %d packet(s), "
                    "resyncing at next keyframe",
                    instance_id,
                    dropped,
                )
                synced = False
            pkt = await _next_packet(sub)
            if pkt is None:
                # ``None`` is either the end-of-stream sentinel (scrcpy
                # client closed — wake-up was instant) or the 5s idle
                # timeout. Distinguish by checking the client's liveness
                # so logs match what actually happened.
                if client.is_alive():
                    logger.warning(
                        "h264 stream %s: no NAL packets for %.1fs — closing",
                        instance_id,
                        _NAL_IDLE_TIMEOUT_S,
                    )
                else:
                    logger.info(
                        "h264 stream %s: scrcpy closed — exiting",
                        instance_id,
                    )
                break
            if pkt.is_config:
                # Forward so the client refreshes its cached SPS+PPS on any
                # codec change (resolution / profile). The client overwrites
                # its cache idempotently if bytes are identical to the cached
                # config we already sent.
                await websocket.send_bytes(_pack(pkt))
                continue
            if not synced:
                # Wait for a keyframe so the first decoded chunk has a valid
                # reference frame. Otherwise WebCodecs raises
                # "decoder needs a keyframe".
                if not pkt.is_key:
                    continue
                synced = True
            await websocket.send_bytes(_pack(pkt))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("h264 stream failed for %s", instance_id)
    finally:
        client.unsubscribe_video(sub)
        if websocket.client_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()
