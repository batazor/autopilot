"""DSL exec handlers for the event calendar.

* ``read_calendar_screen`` (high-priority cron) — the once-per-state screen read:
  navigate → tap each bar → OCR the popup → store the schedule in SQLite, then
  refresh the shared cache + this player's flags.
* ``read_calendar`` (cheap, frequent) — fan this player's live flags out from the
  SQLite schedule into ``wos:player:<id>:state`` for the stamina allocator's
  ``active_when`` conditions.
* ``capture_calendar`` — scroll-to-bottom frame capture (diagnostic).
* ``calendar_goto`` — navigate to an event via its popup's Go button.

SQLite is the single source of truth — no declarative catalog, no fallback. If a
player has no flags, the schedule hasn't been read yet (run the screen read),
which is a wiring problem to fix, not a value to guess.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from games.wos.core.calendar import schedule
from games.wos.core.calendar.adapter import (
    DEFAULT_DAYS,
    acquire_refresh_lock,
    apply_flags_to_player,
    write_shared,
)
from games.wos.core.calendar.capture import scroll_capture_calendar

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)


def _as_days(value: object) -> int:
    try:
        days = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return days if days > 0 else DEFAULT_DAYS


def _resolve_state(player_id: str) -> str:
    """The player's game state/server number (OCR'd by who_i_am into the state
    store). Empty when identity hasn't been captured yet."""
    try:
        from config.state_store import get_state_store

        store = get_state_store().get(player_id)
        if store is None:
            return ""
        return str(store.get("state") or "").strip()
    except Exception:
        logger.debug("calendar: state lookup failed for player=%s", player_id, exc_info=True)
        return ""


async def _publish_schedule(
    ctx: DslExecContext, state: str, events: list[schedule.ScheduleEvent], now: float, *, days: int
) -> dict:
    """Build the view from typed events, cache it, and fan flags to this player."""
    moment = datetime.fromtimestamp(now, tz=UTC)
    view = schedule.build_view(events, moment, days=days)
    await write_shared(ctx.redis_client, state, view, now)
    # Live event_<slug> flags + the live-or-imminent reserve flags (e.g.
    # joe_event_active) the stamina budget + intel reserve gate on. Reserve
    # flags only when the schedule is actually read — an empty schedule means
    # "not read yet", so we assert nothing rather than clear to 0 blindly.
    flags = dict(view["flags"])
    if events:
        flags.update(schedule.reserve_flags(events, moment))
    await apply_flags_to_player(ctx.redis_client, ctx.player_id, flags)
    return view


async def _exec_read_calendar(ctx: DslExecContext) -> None:
    """Fan this player's live event flags out from the SQLite schedule."""
    days = _as_days((ctx.args or {}).get("days"))
    if ctx.redis_client is None or not ctx.player_id:
        ctx.result.update({"action": "no_target"})
        return
    state = _resolve_state(ctx.player_id)
    if not state:
        ctx.result.update({"action": "no_state"})
        return

    from games.wos.core.calendar import db

    events = schedule.parse_rows(db.get_state_schedule(state))
    now = time.time()
    view = await _publish_schedule(ctx, state, events, now, days=days)
    active = sorted(f for f, v in view["flags"].items() if v)
    ctx.result.update(
        {
            "action": "fanned_out" if events else "no_schedule",
            "state": state,
            "events": len(events),
            "active_flags": active,
        }
    )


async def _exec_capture_calendar(ctx: DslExecContext) -> None:
    """Scroll the calendar to the bottom, capturing every event row (diagnostic)."""
    from tasks import dsl_runtime

    actions = dsl_runtime.bot_actions()
    try:
        frames = await scroll_capture_calendar(actions, ctx.instance_id)
    except Exception:
        logger.exception("calendar capture failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "capture_failed"})
        return
    ctx.result.update({"action": "captured", "frames": len(frames), "swipes": max(0, len(frames) - 1)})


async def _exec_read_calendar_screen(ctx: DslExecContext) -> None:
    """Read the real schedule off the calendar and persist it for the state.

    Runs on ``event.calendar`` (scenario ``node``). Single reader per state: the
    SET-NX lock means whichever bot gets here first does the full tap-each-bar →
    parse-popup → swipe scan; the others skip. The deduped events are written to
    SQLite, then the shared cache + this player's flags are refreshed so strategy
    sees the new schedule immediately.
    """
    from games.wos.core.calendar import db
    from games.wos.core.calendar.reader import scan_calendar

    from tasks import dsl_runtime

    if ctx.redis_client is None or not ctx.player_id:
        ctx.result.update({"action": "no_target"})
        return
    state = _resolve_state(ctx.player_id)
    if not state:
        ctx.result.update({"action": "no_state"})
        return
    if not await acquire_refresh_lock(ctx.redis_client, state, ttl=300):
        ctx.result.update({"action": "skip_locked", "state": state})
        return

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    try:
        events = await scan_calendar(actions, ctx.instance_id, ocr._run_tesseract)
    except Exception:
        logger.exception("calendar screen read failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "read_failed", "state": state})
        return

    written = db.replace_state_schedule(
        state, [(e.name, e.starts_at, e.ends_at) for e in events], source="popup_ocr"
    )
    typed = [(e.name, e.starts_at, e.ends_at) for e in events]
    await _publish_schedule(ctx, state, typed, time.time(), days=DEFAULT_DAYS)
    ctx.result.update(
        {"action": "read", "state": state, "events": written, "names": [e.name for e in events]}
    )
    logger.info("calendar: state=%s read %d events from screen", state, written)


def _goto_aliases(args: dict) -> list[str]:
    """Names to match the target event's popup against.

    From explicit ``aliases``/``name`` and/or an ``event`` slug (de-slugged to
    words, e.g. ``foundry_battle`` → ``foundry battle``). No catalog lookup.
    """
    aliases: list[str] = []
    raw = args.get("aliases")
    if isinstance(raw, list):
        aliases += [str(a) for a in raw]
    if args.get("name"):
        aliases.append(str(args["name"]))
    event_id = str(args.get("event") or "").strip()
    if event_id:
        aliases.append(event_id.replace("_", " "))
    seen: set[str] = set()
    return [a for a in (x.strip() for x in aliases) if a and not (a.lower() in seen or seen.add(a.lower()))]


async def _exec_calendar_goto(ctx: DslExecContext) -> None:
    """Navigate to an event via its calendar popup's Go button.

    Runs on ``event.calendar`` (scenario ``node``). Args: ``event`` (slug) and/or
    ``aliases``/``name`` to match the popup title against.
    """
    from games.wos.core.calendar.go_nav import navigate_via_go

    from tasks import dsl_runtime

    aliases = _goto_aliases(ctx.args or {})
    if not aliases:
        ctx.result.update({"action": "no_target"})
        return
    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    try:
        found = await navigate_via_go(actions, ctx.instance_id, ocr._run_tesseract, aliases)
    except Exception:
        logger.exception("calendar go-nav failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "error", "aliases": aliases})
        return
    ctx.result.update({"action": "went" if found else "not_found", "aliases": aliases})


DSL_EXEC_HANDLERS = {
    "read_calendar": _exec_read_calendar,
    "capture_calendar": _exec_capture_calendar,
    "read_calendar_screen": _exec_read_calendar_screen,
    "calendar_goto": _exec_calendar_goto,
}
