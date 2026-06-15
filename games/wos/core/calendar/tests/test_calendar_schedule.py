"""Tests for the pure SQLite-backed schedule math."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from games.wos.core.calendar import schedule

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _ev(name, d0, h0, d1, h1):
    return (name, datetime(2026, 6, d0, h0, tzinfo=UTC), datetime(2026, 6, d1, h1, tzinfo=UTC))


def test_slug_and_event_flag():
    assert schedule.slug("Foundry Battle") == "foundry_battle"
    assert schedule.slug("Mia's Fortune Hut!") == "mia_s_fortune_hut"
    assert schedule.event_flag("Foundry Battle") == "event_foundry_battle"
    assert schedule.event_flag("!!!") == ""


def test_schedule_flags_active_and_inactive():
    events = [_ev("Live", 15, 0, 16, 0), _ev("Later", 18, 0, 19, 0)]
    flags = schedule.schedule_flags(events, NOW)
    assert flags == {"event_live": 1, "event_later": 0}


def test_reserve_flags_active_or_imminent():
    # NOW = 2026-06-15 12:00; default lead 12h → arms through 2026-06-16 00:00.
    live = _ev("Crazy Joe", 15, 8, 15, 20)          # open right now
    imminent = _ev("Crazy Joe", 15, 20, 16, 4)      # starts in 8h (within lead)
    far = _ev("Crazy Joe", 16, 12, 16, 20)          # starts in 24h (beyond lead)
    ended = _ev("Crazy Joe", 14, 0, 15, 8)          # already over
    other = _ev("Foundry Battle", 15, 8, 15, 20)    # unrelated, live

    assert schedule.reserve_flags([live], NOW) == {"joe_event_active": 1}
    assert schedule.reserve_flags([imminent], NOW) == {"joe_event_active": 1}
    assert schedule.reserve_flags([far], NOW) == {"joe_event_active": 0}
    assert schedule.reserve_flags([ended], NOW) == {"joe_event_active": 0}
    # The mapped flag is always emitted (0 when its event is absent), so a stale
    # 1 is cleared once the window closes.
    assert schedule.reserve_flags([other], NOW) == {"joe_event_active": 0}
    assert schedule.reserve_flags([], NOW) == {"joe_event_active": 0}


def test_build_view_active_upcoming_digest():
    events = [_ev("Live All Day", 15, 0, 16, 0), _ev("Tomorrow", 16, 8, 16, 10)]
    view = schedule.build_view(events, NOW, days=3)
    assert [e["name"] for e in view["active"]] == ["Live All Day"]
    assert [e["name"] for e in view["upcoming"]] == ["Tomorrow"]
    assert view["upcoming"][0]["in_hours"] > 0
    assert [b["date"] for b in view["digest"]] == ["2026-06-15", "2026-06-16", "2026-06-17"]
    assert view["flags"] == {"event_live_all_day": 1, "event_tomorrow": 0}
    # the all-day event appears in today's bucket, active
    today = view["digest"][0]["events"]
    assert today[0]["active_now"] is True


def test_flags_from_digest_recomputes_window():
    events = [_ev("Noon", 15, 11, 15, 13)]
    digest = schedule.build_view(events, NOW, days=1)["digest"]
    assert schedule.flags_from_digest(digest, NOW) == {"event_noon": 1}        # 12:00 inside
    later = NOW.replace(hour=14)
    assert schedule.flags_from_digest(digest, later) == {"event_noon": 0}      # window closed


@dataclass
class _Row:
    name: str
    starts_at: str
    ends_at: str


def test_parse_rows_drops_bad_timestamps():
    rows = [
        _Row("Good", "2026-06-15T00:00:00+00:00", "2026-06-16T00:00:00+00:00"),
        _Row("Bad", "not-a-date", "2026-06-16T00:00:00+00:00"),
    ]
    parsed = schedule.parse_rows(rows)
    assert [p[0] for p in parsed] == ["Good"]
