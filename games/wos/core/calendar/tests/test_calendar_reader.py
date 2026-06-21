"""Tests for the calendar screen-reader's pure helpers."""
from __future__ import annotations

from datetime import UTC, datetime

from games.wos.core.calendar.parser import PopupEvent
from games.wos.core.calendar.reader import dedup_events


def _ev(name, d0, d1):
    return PopupEvent(name, datetime(2026, 6, d0, tzinfo=UTC), datetime(2026, 6, d1, tzinfo=UTC))


def test_dedup_collapses_repeats_keeps_first_order():
    events = [_ev("Mia's", 12, 14), _ev("Hero Rally", 13, 16), _ev("Mia's", 12, 14)]
    out = dedup_events(events)
    assert [e.name for e in out] == ["Mia's", "Hero Rally"]


def test_dedup_keeps_distinct_occurrences():
    # same name, different start = different occurrence → both kept
    out = dedup_events([_ev("Crazy Joe", 13, 14), _ev("Crazy Joe", 20, 21)])
    assert len(out) == 2
