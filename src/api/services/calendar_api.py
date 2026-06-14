"""Read the stored per-state event schedule for the dashboard.

Pure-ish view builder over the SQLite ``calendar_events`` table (the schedule
the bot reads off the in-game calendar). No Redis, no device — the dashboard
just renders what the last screen read persisted.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from games.wos.core.calendar import db, schedule


def _state_view(state: str, now: float, *, days: int, game: str) -> dict[str, Any]:
    rows = db.get_state_schedule(state, game=game)
    events = schedule.parse_rows(rows)
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
    """All states' schedules: active / upcoming / full event list per state."""
    now = time.time()
    states = db.list_states(game=game)
    return {
        "game": game,
        "now": datetime.fromtimestamp(now, tz=UTC).isoformat(),
        "days": days,
        "states": [_state_view(s, now, days=days, game=game) for s in states],
    }
