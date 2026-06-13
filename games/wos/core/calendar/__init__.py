"""Event calendar: declarative schedule catalog + look-ahead query core.

Pure decision input for the strategy layer. :class:`~.model.Calendar` parses
``events.yaml`` and answers "what's live now" / "what starts in the next few
days"; the Redis-backed :mod:`~.adapter` publishes that digest and the
per-event active flags into player state so conditions elsewhere (e.g. the
stamina allocator's ``active_when``) can gate on a live event.
"""
from __future__ import annotations

from .model import Calendar, CalendarEvent, Occurrence

__all__ = ["Calendar", "CalendarEvent", "Occurrence"]
