"""Redis-backed adapter: turn the pure :class:`~.model.Calendar` into live
player state.

Split so the schedule logic stays unit-testable:

* :func:`build_view` is pure — given a :class:`Calendar` and a unix ``now`` it
  returns a JSON-able snapshot (digest, upcoming list, active flags). No IO.
* :func:`publish` is the thin async side-effect that writes that snapshot into
  ``wos:player:<id>:state`` so the dashboard and other modules can read it.

The active flags land as plain ``flag -> "1"|"0"`` hash fields, which is exactly
what the stamina allocator's ``active_when`` conditions read (see
``games/wos/core/stamina/adapter.py``) — that's the integration seam: the
calendar decides *what's on*, other modules decide *what to do about it*.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import DEFAULT_CATALOG_PATH, Calendar

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Default look-ahead: today + the next two server-days ("today and the next
# couple of days"). Override via the read_calendar scenario's `days` arg.
DEFAULT_DAYS = 3

_CATALOG_CACHE: dict[str, tuple[float, Calendar]] = {}


def load_catalog(path: str | Path | None = None) -> Calendar:
    """``Calendar.load()`` cached by file mtime — avoids re-reading events.yaml
    on every tick while still picking up edits (mtime change invalidates)."""
    p = Path(path) if path else DEFAULT_CATALOG_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _CATALOG_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    calendar = Calendar.load(p)
    _CATALOG_CACHE[key] = (mtime, calendar)
    return calendar


def build_view(calendar: Calendar, now: float, *, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """JSON-able calendar snapshot for one moment. Pure (``now`` = unix ts)."""
    moment = datetime.fromtimestamp(float(now), tz=UTC)
    active = calendar.active_at(moment)
    upcoming = calendar.upcoming(moment, horizon_days=float(days))
    return {
        "now": moment.isoformat(),
        "days": days,
        "active": [
            {
                "id": ev.id,
                "title": ev.title,
                "category": ev.category,
                "scenario": ev.scenario,
                "strategy": ev.strategy,
                "ends": occ.end.isoformat(),
            }
            for ev, occ in active
        ],
        "upcoming": [
            {
                "id": ev.id,
                "title": ev.title,
                "category": ev.category,
                "starts": occ.start.isoformat(),
                "in_hours": round((occ.start - moment).total_seconds() / 3600.0, 1),
            }
            for ev, occ in upcoming
        ],
        "digest": calendar.digest(moment, days=days),
        "flags": calendar.state_flags(moment),
    }


def state_mapping(view: dict[str, Any], now: float) -> dict[str, str]:
    """Flatten a :func:`build_view` snapshot into a Redis hash mapping.

    Event flags are written as top-level fields so ``active_when`` conditions
    can reference them directly; the digest/upcoming lists are JSON blobs for
    the dashboard.
    """
    mapping: dict[str, str] = {
        "calendar_at": str(now),
        "calendar_digest": json.dumps(view["digest"], separators=(",", ":")),
        "calendar_upcoming": json.dumps(view["upcoming"], separators=(",", ":")),
        "calendar_active": json.dumps(view["active"], separators=(",", ":")),
    }
    for flag, value in view["flags"].items():
        mapping[flag] = str(int(value))
    return mapping


async def publish(
    redis: Redis,
    player_id: str,
    calendar: Calendar,
    now: float,
    *,
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    """Compute the snapshot and write it into ``wos:player:<id>:state``.

    Returns the view (for the scenario trace / diagnostics). Best-effort: a
    Redis flap is logged, not raised, so a transient failure never aborts the
    scenario.
    """
    view = build_view(calendar, now, days=days)
    if redis is None or not player_id:
        return view
    try:
        await redis.hset(f"wos:player:{player_id}:state", mapping=state_mapping(view, now))
    except Exception:
        logger.warning("calendar publish failed for player=%s", player_id, exc_info=True)
    return view
