"""Short-lived memory of Intel events the bot has already started.

When the bot sends a march to an Intel target, that pin stays on the board
(in-progress, with a countdown) until the march returns and the reward is
claimed. Without a memory of what is already running, the value-greedy picker
(:func:`detection.select_planned_marker`) re-selects the same top-value pin on
the next pass and burns the run trying to re-start an event that is already
underway.

This module records the *screen coordinates* of each started pin with a short
TTL (default 10 min — about one march round trip), and offers a pure filter
that drops markers near an already-started coordinate. That lets a later
``intel_run`` skip the in-flight pins and start the *next* best event on a
second free march, without waiting for the first to finish.

Storage is a per-player Redis hash ``wos:player:<id>:intel:started`` mapping
``"x,y" -> started_at`` (epoch seconds). Stale fields are pruned on read, and a
whole-key ``expire`` is set as a best-effort backstop so the key disappears once
idle. The filtering (:func:`partition_markers`) is image-free and pure, so it is
unit-testable without cv2 or Redis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# A march round trip plus the brief in-progress window after it returns. Long
# enough that we never re-pick a pin whose march is still out; short enough that
# the slot frees itself if a deploy was aborted before the march left.
STARTED_TTL_SECONDS = 600
# Two detections of the same fixed-position pin land within a few pixels; board
# pins sit ~90 px apart (see the detector fixtures), so this radius matches the
# same pin without ever swallowing a neighbour. Mirrors the detector NMS gap.
STARTED_MATCH_RADIUS_PX = 40


@dataclass(frozen=True, slots=True)
class StartedEvent:
    """One Intel pin the bot started, by board coordinate + when it started."""

    x: int
    y: int
    started_at: float

    @property
    def coords(self) -> tuple[int, int]:
        return (self.x, self.y)


def started_key(player_id: str) -> str:
    return f"wos:player:{player_id}:intel:started"


def _text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return "" if raw is None else str(raw).strip()


def _parse(field: Any, value: Any) -> StartedEvent | None:
    coord = _text(field)
    parts = coord.split(",")
    if len(parts) != 2:
        return None
    try:
        x, y = int(parts[0]), int(parts[1])
        started_at = float(_text(value))
    except (TypeError, ValueError):
        return None
    return StartedEvent(x=x, y=y, started_at=started_at)


async def record_started(
    redis: Any,
    player_id: str,
    x: int,
    y: int,
    *,
    now: float,
    ttl: int = STARTED_TTL_SECONDS,
) -> bool:
    """Remember that the pin at ``(x, y)`` was just started (TTL-bounded)."""
    if redis is None or not player_id:
        return False
    key = started_key(player_id)
    try:
        await redis.hset(key, f"{int(x)},{int(y)}", str(float(now)))
    except Exception:
        logger.debug("intel: record_started failed player=%s", player_id, exc_info=True)
        return False
    # Best-effort whole-key backstop so an idle key self-deletes. Per-field
    # freshness is still enforced by prune-on-read in load_started.
    expire = getattr(redis, "expire", None)
    if expire is not None:
        try:
            await expire(key, int(max(1, ttl)))
        except Exception:
            logger.debug("intel: started expire failed player=%s", player_id, exc_info=True)
    return True


async def load_started(
    redis: Any,
    player_id: str,
    *,
    now: float,
    ttl: int = STARTED_TTL_SECONDS,
) -> list[StartedEvent]:
    """Active started events for this player; prunes (and forgets) stale ones."""
    if redis is None or not player_id:
        return []
    key = started_key(player_id)
    try:
        raw = await redis.hgetall(key)
    except Exception:
        logger.debug("intel: load_started failed player=%s", player_id, exc_info=True)
        return []
    active: list[StartedEvent] = []
    stale: list[Any] = []
    for field, value in (raw or {}).items():
        ev = _parse(field, value)
        if ev is None or (now - ev.started_at) >= ttl:
            # Keep the field exactly as hgetall returned it (bytes on a client
            # without decode_responses) so hdel matches byte-for-byte.
            stale.append(field)
            continue
        active.append(ev)
    if stale:
        hdel = getattr(redis, "hdel", None)
        if hdel is not None:
            try:
                await hdel(key, *stale)
            except Exception:
                logger.debug("intel: started prune failed player=%s", player_id, exc_info=True)
    active.sort(key=lambda e: e.started_at)
    return active


def is_near_started(
    x: int,
    y: int,
    started: list[StartedEvent],
    *,
    radius: int = STARTED_MATCH_RADIUS_PX,
) -> StartedEvent | None:
    """The started event within ``radius`` px of ``(x, y)``, or ``None``."""
    r2 = int(radius) * int(radius)
    for ev in started:
        dx = x - ev.x
        dy = y - ev.y
        if dx * dx + dy * dy <= r2:
            return ev
    return None


def partition_markers(
    markers: list[Any],
    started: list[StartedEvent],
    *,
    radius: int = STARTED_MATCH_RADIUS_PX,
) -> tuple[list[Any], list[Any]]:
    """Split detected markers into ``(fresh, suppressed)`` by proximity to a
    started coordinate.

    Markers only need a ``.center`` with ``.x`` / ``.y`` (the cv2 ``IntelMarker``
    or any duck-type), so this stays image-free and pure.
    """
    if not started:
        return list(markers), []
    fresh: list[Any] = []
    suppressed: list[Any] = []
    for marker in markers:
        center = marker.center
        if is_near_started(center.x, center.y, started, radius=radius) is not None:
            suppressed.append(marker)
        else:
            fresh.append(marker)
    return fresh, suppressed
