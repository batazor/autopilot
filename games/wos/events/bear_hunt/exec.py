"""DSL exec handler for Bear Hunt trap cooldowns.

``read_bear_hunt_cooldowns`` (daily cron) is the once-per-alliance read: on the
event info page (``event.bear_hunt.info``), tap each trap tab, OCR its
``On cooldown`` timer, and persist the next-ready time per trap to SQLite
(:mod:`~.db`, keyed by alliance — the single source of truth). It then fans an
``event_bear_hunt`` flag out to this player's state so strategy can gate on a
trap being ready, exactly like the calendar's event flags.

A SET-NX lock on the alliance means whichever member gets here first does the
read; the rest skip — Bear Hunt is one shared clock for the whole alliance.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from games.wos.events.bear_hunt import db
from games.wos.events.bear_hunt.contribute import contribute_traps, is_maxed, select_targets
from games.wos.events.bear_hunt.reader import read_trap_info

if TYPE_CHECKING:
    from datetime import timedelta

    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 300
_FLAG = "event_bear_hunt"


def _resolve_alliance_from_store(player_id: str) -> str:
    """The player's alliance name from the state store (OCR'd by who_i_am)."""
    if not player_id:
        return ""
    try:
        from config.state_store import get_state_store

        store = get_state_store().get(player_id)
        if store is None:
            return ""
        return str(store.get("alliance.name") or "").strip()
    except Exception:
        logger.debug("bear_hunt: alliance lookup failed for player=%s", player_id, exc_info=True)
        return ""


async def _resolve_alliance(ctx: DslExecContext) -> str:
    """Alliance name from args → state store → live instance hash (best effort)."""
    raw = str((ctx.args or {}).get("alliance_name") or "").strip()
    if raw:
        return raw
    name = _resolve_alliance_from_store(ctx.player_id)
    if name or ctx.redis_client is None:
        return name
    try:
        raw_hash = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state", "alliance.name"
        )
    except Exception:
        return ""
    if isinstance(raw_hash, bytes):
        return raw_hash.decode("utf-8", errors="replace").strip()
    return "" if raw_hash is None else str(raw_hash).strip()


async def _acquire_lock(redis: Any, alliance: str, *, ttl: int = _LOCK_TTL_SECONDS) -> bool:
    """Best-effort single-reader gate per alliance: SET NX EX."""
    if redis is None or not alliance:
        return False
    try:
        return bool(await redis.set(f"wos:alliance:{alliance}:bear_hunt:lock", "1", nx=True, ex=ttl))
    except Exception:
        logger.warning("bear_hunt lock failed for alliance=%s", alliance, exc_info=True)
        return False


def _trap_rows(
    info: dict[str, tuple[timedelta | None, int | None]], moment: datetime
) -> dict[str, tuple[datetime, int | None]]:
    """Turn read_trap_info output into ``{trap_id: (ready_at, level)}`` rows.

    A ``None`` cooldown means the trap is ready right now.
    """
    return {
        tid: (moment + cd if cd is not None else moment, level)
        for tid, (cd, level) in info.items()
    }


async def _exec_read_bear_hunt_cooldowns(ctx: DslExecContext) -> None:
    from games.wos.core.calendar.adapter import apply_flags_to_player

    from tasks import dsl_runtime

    if not ctx.player_id:
        ctx.result.update({"action": "no_target"})
        return
    alliance = await _resolve_alliance(ctx)
    if not alliance:
        ctx.result.update({"action": "no_alliance"})
        return
    if not await _acquire_lock(ctx.redis_client, alliance):
        ctx.result.update({"action": "skip_locked", "alliance": alliance})
        return

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    try:
        info = await read_trap_info(actions, ctx.instance_id, ocr._run_tesseract)
    except Exception:
        logger.exception("bear_hunt read failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "read_failed", "alliance": alliance})
        return

    now = time.time()
    moment = datetime.fromtimestamp(now, tz=UTC)
    traps = _trap_rows(info, moment)
    written = db.upsert_traps(alliance, traps, now=now)

    any_ready = any(ready <= moment for ready, _ in traps.values())
    await apply_flags_to_player(ctx.redis_client, ctx.player_id, {_FLAG: 1 if any_ready else 0})

    ctx.result.update(
        {
            "action": "read",
            "alliance": alliance,
            "traps": written,
            "ready_at": {tid: ready.isoformat() for tid, (ready, _) in traps.items()},
            "levels": {tid: level for tid, (_, level) in traps.items()},
        }
    )
    logger.info("bear_hunt: alliance=%s wrote %d trap(s) ready=%s", alliance, written, any_ready)


async def _exec_contribute_trap_enhancement(ctx: DslExecContext) -> None:
    """Contribute all available arrows into each non-maxed Trap Enhancement tab.

    Runs on ``event.bear_hunt.info``. Reads each trap's level off the info page,
    remembers it per alliance (``bear_hunt_traps.level``), then pours all arrows
    (slider to max) into every non-maxed trap — or into one if both are maxed (to
    still earn the contribution rewards). Per-player action (arrows are personal),
    so no alliance lock.
    """
    from tasks import dsl_runtime

    alliance = await _resolve_alliance(ctx)  # for remembering levels (no lock)
    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    try:
        info = await read_trap_info(actions, ctx.instance_id, ocr._run_tesseract)
    except Exception:
        logger.exception("bear_hunt contribute read failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "read_failed"})
        return

    # Remember levels at the alliance level (both traps, fresh read).
    levels = {tid: level for tid, (_cd, level) in info.items()}
    if alliance:
        moment = datetime.fromtimestamp(time.time(), tz=UTC)
        db.upsert_traps(alliance, _trap_rows(info, moment))

    # Both maxed → select_targets still returns one tab (earn rewards anyway).
    maxed = {tid: is_maxed(level) for tid, (_cd, level) in info.items()}
    targets = select_targets(maxed)
    try:
        results = await contribute_traps(actions, ctx.instance_id, targets)
    except Exception:
        logger.exception("bear_hunt contribute failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "failed"})
        return

    ctx.result.update(
        {
            "action": "contributed",
            "alliance": alliance,
            "levels": levels,
            "targets": targets,
            "traps": results,
        }
    )
    logger.info(
        "bear_hunt contribute: instance=%s levels=%s targets=%s",
        ctx.instance_id, levels, targets,
    )


DSL_EXEC_HANDLERS = {
    "read_bear_hunt_cooldowns": _exec_read_bear_hunt_cooldowns,
    "contribute_trap_enhancement": _exec_contribute_trap_enhancement,
}
