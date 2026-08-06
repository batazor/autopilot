"""Board snapshot: remember what one intel-board scan saw, for one window.

One ``tap_intel_fight`` detection pass sees the whole board. Recording its
outcome — how many actionable pins remain after the pick — lets every later
"should we go look at the board again?" decision answer from memory instead of
paying a full navigate + claim + detect round trip to discover an empty board:

* :func:`chain.queue_next_intel_run` ends the multi-march chain WITHOUT the
  terminating wasted visit (previously the chain only stopped when a pass
  found nothing).
* the MARCH coordinator's blind ``intel_intent`` (``coordinator/dispatch.py``)
  skips enqueueing ``intel_run`` while a fresh snapshot says the board is
  exhausted.

The snapshot expires with the board itself: TTL = the live «Refreshes in»
timer read on the same pass, capped at :data:`BOARD_CACHE_CAP_S` (15 min) so a
misread timer can never freeze intel out for hours. Expired/absent snapshot →
callers behave exactly as before (go look).

Storage: per-player Redis string ``wos:player:<id>:intel:board`` holding JSON
``{"viable_left": int, "detected": int, "captured_at": float}`` with EX=ttl.
Pure helpers (:func:`board_ttl_s`, :func:`viable_left_after`) carry the logic;
the Redis I/O layer is thin and failure-silent like the rest of the module.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Upper bound for how long a snapshot may gate re-visits. The user-facing
# contract: «помним точки, пока не пройдём все или не пройдёт 15 минут».
BOARD_CACHE_CAP_S = 900
# A snapshot that would expire almost immediately isn't worth writing.
_MIN_TTL_S = 5


def board_key(player_id: str) -> str:
    return f"wos:player:{player_id}:intel:board"


def board_ttl_s(refresh_in_s: float | None, *, cap_s: int = BOARD_CACHE_CAP_S) -> int:
    """Seconds a board snapshot stays valid.

    The board's own «Refreshes in» timer is the truth — after it fires the
    board repopulates and the memory is stale by definition. Unknown/garbage
    timer → the cap alone (the 15-min fallback).
    """
    try:
        refresh = float(refresh_in_s) if refresh_in_s is not None else None
    except (TypeError, ValueError):
        refresh = None
    if refresh is None or refresh <= 0:
        return cap_s
    return max(_MIN_TTL_S, min(int(refresh), cap_s))


def viable_left_after(fresh_count: int, *, tapped: bool) -> int:
    """Actionable pins remaining after this pass.

    ``fresh_count`` is the number of detected pins that are not already
    in-flight (pre slot-filter — a fight pin blocked only by "no free march
    slot right now" is still worth a later visit). The tapped pin is committed
    (it enters the started-memory), so it no longer counts.
    """
    return max(0, int(fresh_count) - (1 if tapped else 0))


async def save_board(
    redis: Any,
    player_id: str,
    *,
    detected: int,
    viable_left: int,
    refresh_in_s: float | None,
    now: float,
) -> bool:
    """Persist the snapshot (best-effort; ``False`` on any failure)."""
    if redis is None or not player_id:
        return False
    payload = json.dumps(
        {
            "viable_left": max(0, int(viable_left)),
            "detected": max(0, int(detected)),
            "captured_at": float(now),
        }
    )
    try:
        await redis.set(board_key(player_id), payload, ex=board_ttl_s(refresh_in_s))
    except Exception:
        logger.debug("intel board cache: save failed player=%s", player_id, exc_info=True)
        return False
    return True


async def load_board(redis: Any, player_id: str) -> dict[str, Any] | None:
    """Latest snapshot, or ``None`` when absent/expired/unreadable."""
    if redis is None or not player_id:
        return None
    try:
        raw = await redis.get(board_key(player_id))
    except Exception:
        logger.debug("intel board cache: load failed player=%s", player_id, exc_info=True)
        return None
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def board_exhausted(redis: Any, player_id: str) -> bool:
    """True when a FRESH snapshot says no actionable pins remain.

    Absent/expired snapshot → ``False`` (unknown board — go look, the
    pre-cache behaviour).
    """
    snap = await load_board(redis, player_id)
    if snap is None:
        return False
    try:
        return int(snap.get("viable_left", 1)) <= 0
    except (TypeError, ValueError):
        return False
