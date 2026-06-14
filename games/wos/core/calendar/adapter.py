"""Redis-backed adapter: share the SQLite schedule, fan flags to players.

Events are state-wide and read once into ``db.calendar_events`` (SQLite, the
single source of truth). This module is the thin Redis layer over that:

* The resolved schedule view is cached in a **shared per-state key**
  ``wos:state:<state>:calendar`` (:func:`state_calendar_key`), refreshed under a
  best-effort SET-NX lock so only one bot per state does the work.
* Each player **derives its own live flags** from that cache
  (:func:`derive_flags`) and writes them to ``wos:player:<id>:state``
  (:func:`apply_flags_to_player`) — a Redis-only fan-out the stamina allocator's
  ``active_when`` conditions read.

The schedule math itself lives in :mod:`~.schedule` (pure); here we only move
bytes. No catalog, no fallback — an empty schedule means "not read yet".
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from games.wos.core.calendar.schedule import flags_from_digest

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Default look-ahead: today + the next two server-days.
DEFAULT_DAYS = 3
# Re-share the schedule at most this often per state (day-grained; live flags
# are recomputed fresh from the cached digest regardless).
REFRESH_TTL_SECONDS = 3600
# Single-reader lock lifetime — long enough for a navigate + scan, short enough
# to self-heal if the holder dies mid-read.
LOCK_TTL_SECONDS = 300


def state_calendar_key(state: str) -> str:
    """Shared per-state schedule hash (one read serves every account here)."""
    return f"wos:state:{state}:calendar"


def _refresh_lock_key(state: str) -> str:
    return f"wos:state:{state}:calendar:lock"


def should_refresh(read_at: float | None, now: float, *, ttl: float = REFRESH_TTL_SECONDS) -> bool:
    """True when the shared cache is missing or older than ``ttl`` seconds."""
    if read_at is None:
        return True
    return (float(now) - float(read_at)) >= float(ttl)


def shared_mapping(view: dict[str, Any], now: float, *, source: str = "sqlite") -> dict[str, str]:
    """Flatten a :func:`schedule.build_view` snapshot into the shared hash."""
    return {
        "read_at": str(now),
        "source": source,
        "days": str(view.get("days", DEFAULT_DAYS)),
        "digest": json.dumps(view["digest"], separators=(",", ":")),
        "upcoming": json.dumps(view["upcoming"], separators=(",", ":")),
        "active": json.dumps(view["active"], separators=(",", ":")),
        "flags": json.dumps(view["flags"], separators=(",", ":")),
    }


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


def decode_shared(raw_hash: dict[Any, Any] | None) -> dict[str, Any]:
    """Decode a raw ``wos:state:<state>:calendar`` hash into typed fields."""
    if not raw_hash:
        return {}
    out = {_decode(k): _decode(v) for k, v in raw_hash.items()}
    parsed: dict[str, Any] = {
        "read_at": float(out["read_at"]) if out.get("read_at") else None,
        "source": out.get("source") or "",
        "days": int(out["days"]) if out.get("days") else DEFAULT_DAYS,
    }
    for blob in ("digest", "upcoming", "active", "flags"):
        try:
            parsed[blob] = json.loads(out[blob]) if out.get(blob) else None
        except (ValueError, TypeError):
            parsed[blob] = None
    return parsed


def derive_flags(shared: dict[str, Any], now: float) -> dict[str, int]:
    """Live per-event flags for ``now`` from a decoded shared schedule.

    Recomputed from the digest's windows so flags are correct between refreshes;
    falls back to the stored ``flags`` snapshot if the digest is absent.
    """
    digest = shared.get("digest")
    if isinstance(digest, list):
        return flags_from_digest(digest, datetime.fromtimestamp(float(now), tz=UTC))
    stored = shared.get("flags")
    if isinstance(stored, dict):
        return {str(k): int(v) for k, v in stored.items()}
    return {}


async def read_shared(redis: Redis, state: str) -> dict[str, Any]:
    """Decoded shared schedule for a state (empty dict if absent / on error)."""
    if redis is None or not state:
        return {}
    try:
        raw = await redis.hgetall(state_calendar_key(state))
    except Exception:
        logger.warning("calendar read_shared failed for state=%s", state, exc_info=True)
        return {}
    return decode_shared(raw)


async def write_shared(
    redis: Redis, state: str, view: dict[str, Any], now: float, *, source: str = "sqlite"
) -> None:
    """Persist the schedule view to the shared per-state key."""
    if redis is None or not state:
        return
    try:
        await redis.hset(state_calendar_key(state), mapping=shared_mapping(view, now, source=source))
    except Exception:
        logger.warning("calendar write_shared failed for state=%s", state, exc_info=True)


async def acquire_refresh_lock(redis: Redis, state: str, *, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """Best-effort single-reader gate: SET NX EX. True → this bot may refresh."""
    if redis is None or not state:
        return False
    try:
        ok = await redis.set(_refresh_lock_key(state), "1", nx=True, ex=ttl)
    except Exception:
        logger.warning("calendar lock failed for state=%s", state, exc_info=True)
        return False
    return bool(ok)


async def apply_flags_to_player(redis: Redis, player_id: str, flags: dict[str, int]) -> None:
    """Write derived event flags into ``wos:player:<id>:state`` (cheap fan-out)."""
    if redis is None or not player_id or not flags:
        return
    try:
        await redis.hset(
            f"wos:player:{player_id}:state",
            mapping={str(k): str(int(v)) for k, v in flags.items()},
        )
    except Exception:
        logger.warning("calendar flag fan-out failed for player=%s", player_id, exc_info=True)
