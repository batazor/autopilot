"""Tests for the pure calendar model — recurrence math + look-ahead queries.

Events are built in-test via ``Calendar.from_dict`` and keyed to the weekday of
a fixed ``NOW`` so the assertions stay robust if the shipped events.yaml
schedule changes. One test does load the real catalog to guard its shape.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from games.wos.core.calendar.model import (
    Calendar,
    CalendarEvent,
    parse_hhmm,
    parse_weekday,
)

# A Monday-ish anchor; .weekday() is read in-test, never hard-coded.
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
TODAY_WD = NOW.weekday()
TOMORROW_WD = (TODAY_WD + 1) % 7


def _weekday_name(wd: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]


def test_parse_weekday_names_and_ints():
    assert parse_weekday("Mon") == 0
    assert parse_weekday("sunday") == 6
    assert parse_weekday(5) == 5
    assert parse_weekday(8) == 1  # wraps
    with pytest.raises(ValueError, match="invalid weekday"):
        parse_weekday("someday")


def test_parse_hhmm_handles_end_of_day():
    assert parse_hhmm("00:00") == 0
    assert parse_hhmm("09:30") == 570
    assert parse_hhmm("24:00") == 1440
    assert parse_hhmm("", default=99) == 99
    with pytest.raises(ValueError, match="out of range"):
        parse_hhmm("25:00")


def test_from_dict_rejects_non_positive_window():
    with pytest.raises(ValueError, match="must be after start"):
        CalendarEvent.from_dict(
            {"id": "bad", "recurrence": "weekly", "weekdays": ["Mon"],
             "start": "20:00", "end": "08:00"}
        )


def test_weekly_event_active_only_on_its_weekday():
    cal = Calendar.from_dict({"events": [
        {"id": "today_ev", "recurrence": "weekly",
         "weekdays": [_weekday_name(TODAY_WD)], "start": "00:00", "end": "24:00"},
        {"id": "other_ev", "recurrence": "weekly",
         "weekdays": [_weekday_name(TOMORROW_WD)], "start": "00:00", "end": "24:00"},
    ]})
    active_ids = {ev.id for ev, _ in cal.active_at(NOW)}
    assert active_ids == {"today_ev"}


def test_daily_window_gates_on_time_of_day():
    cal = Calendar.from_dict({"events": [
        {"id": "morning", "recurrence": "daily", "start": "06:00", "end": "11:00"},
        {"id": "midday", "recurrence": "daily", "start": "11:00", "end": "14:00"},
    ]})
    active_ids = {ev.id for ev, _ in cal.active_at(NOW)}  # NOW is 12:00
    assert active_ids == {"midday"}


def test_once_event_overlap():
    ev = {"id": "limited", "recurrence": "once",
          "starts_at": "2026-06-15T10:00:00Z", "ends_at": "2026-06-15T15:00:00Z"}
    cal = Calendar.from_dict({"events": [ev]})
    assert {e.id for e, _ in cal.active_at(NOW)} == {"limited"}
    # Before it starts: not active, but upcoming.
    before = NOW - timedelta(hours=6)
    assert cal.active_at(before) == []
    assert {e.id for e, _ in cal.upcoming(before, horizon_days=1)} == {"limited"}


def test_upcoming_orders_by_start_and_excludes_active():
    cal = Calendar.from_dict({"events": [
        {"id": "live_now", "recurrence": "daily", "start": "00:00", "end": "24:00"},
        {"id": "tomorrow", "recurrence": "weekly",
         "weekdays": [_weekday_name(TOMORROW_WD)], "start": "08:00", "end": "10:00"},
    ]})
    upcoming = cal.upcoming(NOW, horizon_days=3)
    ids = [ev.id for ev, _ in upcoming]
    assert "live_now" not in ids          # already active → not "upcoming"
    assert ids == ["tomorrow"]
    assert all(occ.start > NOW for _, occ in upcoming)


def test_digest_has_one_bucket_per_day_with_active_flag():
    cal = Calendar.from_dict({"events": [
        {"id": "daily_noon", "recurrence": "daily", "start": "11:00", "end": "13:00"},
    ]})
    buckets = cal.digest(NOW, days=3)
    assert [b["date"] for b in buckets] == [
        (NOW + timedelta(days=i)).date().isoformat() for i in range(3)
    ]
    # The daily event appears in every bucket, but active_now only today.
    assert all(len(b["events"]) == 1 for b in buckets)
    assert buckets[0]["events"][0]["active_now"] is True
    assert buckets[1]["events"][0]["active_now"] is False


def test_state_flags_emit_one_and_zero():
    cal = Calendar.from_dict({"events": [
        {"id": "on", "recurrence": "daily", "start": "00:00", "end": "24:00",
         "state_flag": "event_on"},
        {"id": "off", "recurrence": "weekly",
         "weekdays": [_weekday_name(TOMORROW_WD)], "start": "00:00", "end": "24:00",
         "state_flag": "event_off"},
        {"id": "no_flag", "recurrence": "daily", "start": "00:00", "end": "24:00"},
    ]})
    flags = cal.state_flags(NOW)
    assert flags == {"event_on": 1, "event_off": 0}  # no_flag contributes nothing


def test_disabled_event_never_occurs():
    cal = Calendar.from_dict({"events": [
        {"id": "muted", "recurrence": "daily", "start": "00:00", "end": "24:00",
         "enabled": False},
    ]})
    assert cal.active_at(NOW) == []
    assert cal.digest(NOW, days=1)[0]["events"] == []


def test_shipped_catalog_loads():
    cal = Calendar.load()
    assert cal.events  # catalog is non-empty
    # Every shipped entry parses and exposes a stable id.
    assert all(ev.id for ev in cal.events)
