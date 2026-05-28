"""Shared helpers for reporting live scrcpy stream availability to the UI.

The dashboard surfaces a ``stream.available`` flag on multiple endpoints
(click-approval view, per-instance detail) so the browser can report whether
the H.264 socket would actually serve data. Centralising the check here
prevents the two routes from drifting and keeps the lookup side-effect free
(no implicit client construction from a UI probe).
"""
from __future__ import annotations

from adb.scrcpy import lookup_scrcpy_client
from config.loader import load_settings


def scrcpy_stream_available(instance_id: str) -> bool:
    """True iff the live H.264 WebSocket endpoint can stream this instance.

    Requires:
      * worker has already mapped this instance to a device serial,
      * a ``ScrcpyClient`` for that serial is registered AND alive,
      * the reader thread has buffered at least one config packet (SPS+PPS),
        without which WebCodecs can't be configured on the browser side.

    Pure lookup — never creates a client. The dashboard polls this on every
    refresh, so making it side-effect-free keeps the registry clean even
    when the operator is on a device with a non-scrcpy backend.
    """
    serial: str | None = None
    for inst in load_settings().instances:
        if inst.instance_id == instance_id:
            serial = inst.bluestacks_window_title
            break
    if not serial:
        return False
    client = lookup_scrcpy_client(serial)
    if client is None or not client.is_alive():
        return False
    return client.latest_codec_config() is not None
