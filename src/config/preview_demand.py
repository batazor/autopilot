"""Demand signal for the rolling preview PNG.

Every worker tick encodes a full-resolution lossless PNG of the device screen
and writes it to ``temporal/``. That is the single most expensive step of a tick
(~18 ms on a 720x1280 frame, several times the cost of everything else combined)
and it ran unconditionally — including on an unwatched instance whose preview
nobody would look at before it was overwritten.

The live JPEG stream already has a "someone is watching" flag
(``screen_viewers``, set by the stream endpoint). It cannot be reused here
because two *other* consumers read the PNG file rather than the stream:

* ``GET /api/instances/{id}/preview`` — the instance page's polled image.
* ``uv run botctl screenshot`` — how an agent shows the operator a screen.

Both mark demand through this module, so the worker keeps a fresh preview
exactly while something is consuming it and falls back to a slow keepalive
otherwise. The keepalive still refreshes the file periodically, so the mtime
stays meaningful for liveness heuristics and a preview is never absent.

Key/TTL live here (rather than beside the stream helpers in ``api.services``)
so the worker, the API and the CLI can all import them without depending on
each other.
"""
from __future__ import annotations

import contextlib
from typing import Any

_DEMAND_KEY_FMT = "wos:instance:{instance_id}:preview_demand"

# How long one read keeps the preview on its fast cadence. Comfortably longer
# than the instance page's ~1 s poll, so a watching tab never lets it lapse,
# and short enough that closing the tab restores the cheap cadence promptly.
DEMAND_TTL_S = 20

# Cadence the preview falls back to when nothing is reading it. Bounds how stale
# a ``botctl screenshot`` can be before that command captures fresh itself.
UNWATCHED_KEEPALIVE_S = 20.0


def demand_key(instance_id: str) -> str:
    return _DEMAND_KEY_FMT.format(instance_id=instance_id)


def mark_preview_demand(client: Any, instance_id: str) -> None:
    """Record that something just read this instance's preview (best effort)."""
    # A preview read must never fail because Redis is unavailable; the worker
    # simply keeps its keepalive cadence.
    with contextlib.suppress(Exception):
        client.set(demand_key(instance_id), "1", ex=DEMAND_TTL_S)
