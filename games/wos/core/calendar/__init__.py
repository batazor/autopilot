"""Event calendar: read the live schedule, store it in SQLite, share it.

The on-screen calendar is read once per state into ``db.calendar_events``
(SQLite, single source of truth); :mod:`~.schedule` turns those rows into the
per-event flags strategy gates on and the digest the dashboard renders, and the
Redis-backed :mod:`~.adapter` caches + fans them out per player.
"""
from __future__ import annotations

from games.wos.core.calendar.parser import PopupEvent
from games.wos.core.calendar.schedule import event_flag, slug

__all__ = ["PopupEvent", "event_flag", "slug"]
