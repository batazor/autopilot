"""Pure schedule math over the SQLite-backed per-state event schedule.

The schedule is read off the live calendar and stored in
``db.calendar_events`` — concrete ``(name, start, end)`` occurrences, no
declarative catalog and no recurrence. These helpers turn those rows into the
two things downstream needs:

* **flags** (``event_<slug>`` → 1 while live) — what strategy gates on via the
  stamina allocator's ``active_when`` conditions.
* **a view** (active / upcoming / per-day digest) — what the dashboard renders
  and what the shared Redis key caches.

SQLite is the single source of truth: if a flag is missing, the schedule hasn't
been read yet (run ``read_calendar_screen``), not "fall back to a guess".

Pure — datetimes in, plain dicts out; unit-tested without Redis or a DB.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

# (name, starts_at, ends_at) — the normalized shape every helper consumes.
ScheduleEvent = tuple[str, datetime, datetime]


def slug(name: str) -> str:
    """Stable identifier from an event name: ``"Foundry Battle" → "foundry_battle"``."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def event_flag(name: str) -> str:
    """Player-state flag key for an event (``"" `` when the name has no usable slug)."""
    s = slug(name)
    return f"event_{s}" if s else ""


def parse_rows(rows: Any) -> list[ScheduleEvent]:
    """Normalize ``db.CalendarEventRow``s (ISO strings) to typed events.

    Rows with unparseable timestamps are dropped rather than aborting the whole
    schedule — one bad OCR read shouldn't blank the dashboard.
    """
    out: list[ScheduleEvent] = []
    for r in rows:
        try:
            start = datetime.fromisoformat(r.starts_at)
            end = datetime.fromisoformat(r.ends_at)
        except (AttributeError, TypeError, ValueError):
            continue
        out.append((str(r.name), start, end))
    return out


def schedule_flags(events: list[ScheduleEvent], now: datetime) -> dict[str, int]:
    """``event_<slug> -> 1|0`` for every event in the schedule (1 while live)."""
    flags: dict[str, int] = {}
    for name, start, end in events:
        flag = event_flag(name)
        if not flag:
            continue
        active = start <= now < end
        flags[flag] = 1 if active else flags.get(flag, 0)
    return flags


def _event_row(name: str, start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
    return {
        "name": name,
        "state_flag": event_flag(name),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "active_now": start <= now < end,
    }


def build_view(events: list[ScheduleEvent], now: datetime, *, days: int) -> dict[str, Any]:
    """Active / upcoming / per-day digest + flags snapshot for one moment."""
    horizon = now + timedelta(days=days)
    active = [(n, s, e) for n, s, e in events if s <= now < e]
    upcoming = sorted(
        [(n, s, e) for n, s, e in events if s > now and s <= horizon], key=lambda x: x[1]
    )
    today = datetime(now.year, now.month, now.day, tzinfo=UTC)
    digest: list[dict[str, Any]] = []
    for i in range(max(1, days)):
        d0 = today + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        rows = sorted(
            (
                _event_row(n, s, e, now)
                for n, s, e in events
                if s < d1 and e > d0  # occurrence overlaps this day
            ),
            key=lambda r: r["start"],
        )
        digest.append({"date": d0.date().isoformat(), "events": rows})
    return {
        "now": now.isoformat(),
        "days": days,
        "active": [
            {"name": n, "state_flag": event_flag(n), "ends": e.isoformat()}
            for n, s, e in active
        ],
        "upcoming": [
            {
                "name": n,
                "starts": s.isoformat(),
                "in_hours": round((s - now).total_seconds() / 3600.0, 1),
            }
            for n, s, e in upcoming
        ],
        "digest": digest,
        "flags": schedule_flags(events, now),
    }


def flags_from_digest(digest: list[dict[str, Any]], now: datetime) -> dict[str, int]:
    """Re-derive live flags from a stored digest at ``now`` (no DB hit).

    Lets each player compute its own flags from the cached shared schedule
    between reads; ``active_now`` is recomputed from each row's ``start``/``end``.
    """
    flags: dict[str, int] = {}
    for bucket in digest:
        for row in bucket.get("events", []):
            flag = row.get("state_flag")
            if not flag:
                continue
            try:
                start = datetime.fromisoformat(row["start"])
                end = datetime.fromisoformat(row["end"])
            except (KeyError, TypeError, ValueError):
                continue
            flags[flag] = 1 if start <= now < end else flags.get(flag, 0)
    return flags
