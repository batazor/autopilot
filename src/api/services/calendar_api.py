"""Read the stored per-state event schedule for the dashboard.

Pure-ish view builder over the SQLite ``calendar_events`` table (the schedule
the bot reads off the in-game calendar). No Redis, no device — the dashboard
just renders what the last screen read persisted.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from games.wos.core.calendar import db, schedule

logger = logging.getLogger(__name__)


def _alliances_by_state(game: str) -> dict[str, set[str]]:
    """Map each in-game state → the alliance names present on it (from gamers).

    Bear Hunt is stored per-alliance but the calendar view is per-state, so we
    use the player roster to project each alliance's traps onto its state.
    """
    out: dict[str, set[str]] = {}
    try:
        from config.state_sqlite import list_gamers_by_power

        for gamer in list_gamers_by_power(0, game=game):
            st = str(gamer.state or "").strip()
            alliance = str(gamer.alliance.name or "").strip()
            if st and alliance:
                out.setdefault(st, set()).add(alliance)
    except Exception:
        logger.debug("calendar: alliance-by-state lookup failed", exc_info=True)
    return out


def _bear_hunt_events(alliances: set[str], game: str) -> list[schedule.ScheduleEvent]:
    """Bear Hunt trap occurrences for a set of alliances as schedule events."""
    from games.wos.events.bear_hunt import db as bh_db

    events: list[schedule.ScheduleEvent] = []
    for alliance in alliances:
        for row in bh_db.get_traps(alliance, game=game):
            try:
                start = datetime.fromisoformat(row.ready_at)
            except (TypeError, ValueError):
                continue
            end = start + timedelta(minutes=row.window_minutes)
            events.append((f"Bear Hunt Trap {row.trap_id}", start, end))
    return events


def _state_view(
    state: str,
    now: float,
    *,
    days: int,
    game: str,
    extra_events: list[schedule.ScheduleEvent] | None = None,
) -> dict[str, Any]:
    rows = db.get_state_schedule(state, game=game)
    events = schedule.parse_rows(rows)
    if extra_events:
        events.extend(extra_events)
    moment = datetime.fromtimestamp(now, tz=UTC)
    view = schedule.build_view(events, moment, days=days)
    updated_at = max((r.updated_at for r in rows), default=None)
    return {
        "state": state,
        "updated_at": updated_at,
        "event_count": len(events),
        "active": view["active"],
        "upcoming": view["upcoming"],
        "events": [
            {
                "name": name,
                "state_flag": schedule.event_flag(name),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "active_now": start <= moment < end,
            }
            for name, start, end in sorted(events, key=lambda e: e[1])
        ],
    }


def build_calendar_view(*, game: str = "wos", days: int = 7) -> dict[str, Any]:
    """All states' schedules: active / upcoming / full event list per state.

    Per-state in-game events come from ``calendar_events``; per-alliance Bear
    Hunt traps are projected onto each state via the player roster.
    """
    now = time.time()
    alliances_by_state = _alliances_by_state(game)
    # Include states that only have Bear Hunt data (no calendar read yet).
    states = sorted(set(db.list_states(game=game)) | set(alliances_by_state))
    return {
        "game": game,
        "now": datetime.fromtimestamp(now, tz=UTC).isoformat(),
        "days": days,
        "states": [
            _state_view(
                s,
                now,
                days=days,
                game=game,
                extra_events=_bear_hunt_events(alliances_by_state.get(s, set()), game),
            )
            for s in states
        ],
    }
