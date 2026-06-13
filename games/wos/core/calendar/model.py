"""Pure data model + scheduling math for the in-game event calendar.

No Redis, no ADB, no game IO — every function here is deterministic and unit
testable. The Redis-backed adapter (:mod:`adapter`) resolves the live "now"
and publishes the computed digest + active flags into player state.

The point of this module: let the bot *look ahead*. Whiteout Survival runs a
rotating set of events (combat, growth, alliance, limited-time). Knowing what
is live right now and what starts in the next day or two lets the strategy
layer (e.g. the stamina allocator's ``active_when`` conditions, see
``games/wos/core/stamina``) hold resources for a beast event tonight instead of
burning them this afternoon.

Two complementary inputs feed a :class:`Calendar`:

* ``events.yaml`` — the declarative catalog of *recurring* events whose cadence
  is known (weekly/daily windows in server time). This is what we can schedule
  ahead of time without ever opening the game.
* the on-screen calendar (OCR, a later step) — overrides/augments the catalog
  with limited-time events that have explicit start/end datetimes. These enter
  the model as ``recurrence: once`` entries.

All times are server time, treated as **UTC** (WOS event timers are UTC-based).
Weekday windows that cross midnight are modelled as ``once`` entries or as two
weekday windows; a single weekly window stays within one server-day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = _MODULE_DIR / "events.yaml"

# Mon=0 .. Sun=6, matching datetime.weekday().
_WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

# Recurrence kinds.
WEEKLY = "weekly"
DAILY = "daily"
ONCE = "once"
_VALID_RECURRENCE = frozenset({WEEKLY, DAILY, ONCE})

_MINUTES_PER_DAY = 24 * 60


def parse_weekday(value: Any) -> int:
    """Coerce a weekday (``"Mon"`` / full name / int 0-6) to Mon=0..Sun=6."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        msg = f"invalid weekday type: {value!r}"
        raise TypeError(msg)
    if isinstance(value, int):
        return value % 7
    key = str(value).strip().lower()
    if key in _WEEKDAY_NAMES:
        return _WEEKDAY_NAMES[key]
    if key.isdigit():
        return int(key) % 7
    msg = f"invalid weekday: {value!r}"
    raise ValueError(msg)


def parse_hhmm(value: Any, *, default: int = 0) -> int:
    """Minutes since midnight for an ``"HH:MM"`` string.

    Accepts ``"24:00"`` (== 1440) as an exclusive end-of-day marker so a window
    can span a whole server-day. Empty/missing → ``default``.
    """
    if value is None or value == "":
        return default
    parts = str(value).strip().split(":")
    hours = int(parts[0]) if parts[0] != "" else 0
    minutes = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
    total = hours * 60 + minutes
    if not 0 <= total <= _MINUTES_PER_DAY:
        msg = f"time out of range: {value!r}"
        raise ValueError(msg)
    return total


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 datetime to an aware UTC datetime (naive → assume UTC)."""
    if value is None or value == "":
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Occurrence:
    """A concrete [start, end) interval of one event, in UTC."""

    start: datetime
    end: datetime

    def contains(self, now: datetime) -> bool:
        return self.start <= now < self.end

    def overlaps(self, w_start: datetime, w_end: datetime) -> bool:
        return self.start < w_end and self.end > w_start


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One catalog entry. Recurring (weekly/daily window) or one-off (explicit)."""

    id: str
    title: str
    category: str = "general"
    recurrence: str = WEEKLY
    weekdays: tuple[int, ...] = ()       # weekly only; Mon=0..Sun=6
    start_min: int = 0                   # minutes since midnight (weekly/daily)
    end_min: int = _MINUTES_PER_DAY      # exclusive; 1440 == end of server-day
    starts_at: datetime | None = None    # once only
    ends_at: datetime | None = None      # once only
    state_flag: str | None = None        # flat state key set 1/0 by the adapter
    scenario: str | None = None          # linked DSL scenario, if any
    strategy: str = ""                   # operator-facing hint
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CalendarEvent:
        recurrence = str(raw.get("recurrence") or WEEKLY).strip().lower()
        if recurrence not in _VALID_RECURRENCE:
            msg = f"event {raw.get('id')!r}: bad recurrence {recurrence!r}"
            raise ValueError(msg)
        start_min = parse_hhmm(raw.get("start"), default=0)
        end_min = parse_hhmm(raw.get("end"), default=_MINUTES_PER_DAY)
        if recurrence in (WEEKLY, DAILY) and end_min <= start_min:
            msg = (
                f"event {raw.get('id')!r}: end {end_min} must be after start {start_min} "
                "(model midnight-crossing windows as `once` or split weekdays)"
            )
            raise ValueError(msg)
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title") or raw["id"]),
            category=str(raw.get("category") or "general"),
            recurrence=recurrence,
            weekdays=tuple(sorted({parse_weekday(w) for w in (raw.get("weekdays") or [])})),
            start_min=start_min,
            end_min=end_min,
            starts_at=_parse_dt(raw.get("starts_at")),
            ends_at=_parse_dt(raw.get("ends_at")),
            state_flag=(str(raw["state_flag"]).strip() if raw.get("state_flag") else None),
            scenario=(str(raw["scenario"]).strip() if raw.get("scenario") else None),
            strategy=str(raw.get("strategy") or ""),
            enabled=bool(raw.get("enabled", True)),
        )

    def occurrences_in(self, w_start: datetime, w_end: datetime) -> list[Occurrence]:
        """Every occurrence of this event overlapping ``[w_start, w_end)``."""
        if not self.enabled or w_end <= w_start:
            return []
        if self.recurrence == ONCE:
            if self.starts_at is None or self.ends_at is None:
                return []
            occ = Occurrence(self.starts_at, self.ends_at)
            return [occ] if occ.overlaps(w_start, w_end) else []

        out: list[Occurrence] = []
        # Pad by a day each side so a window's edge can't clip an occurrence
        # whose start sits just outside the [w_start, w_end) date range.
        day = (w_start - timedelta(days=1)).date()
        last = (w_end + timedelta(days=1)).date()
        while day <= last:
            if self.recurrence == DAILY or day.weekday() in self.weekdays:
                midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
                occ = Occurrence(
                    midnight + timedelta(minutes=self.start_min),
                    midnight + timedelta(minutes=self.end_min),
                )
                if occ.overlaps(w_start, w_end):
                    out.append(occ)
            day += timedelta(days=1)
        out.sort(key=lambda o: o.start)
        return out


@dataclass(frozen=True, slots=True)
class Calendar:
    """Parsed ``events.yaml`` — the declarative event catalog for one game."""

    events: tuple[CalendarEvent, ...] = ()
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Calendar:
        raw = raw or {}
        return cls(
            events=tuple(
                CalendarEvent.from_dict(e) for e in (raw.get("events") or [])
            ),
            enabled=bool(raw.get("enabled", True)),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> Calendar:
        p = Path(path) if path else DEFAULT_CATALOG_PATH
        return cls.from_dict(yaml.safe_load(p.read_text(encoding="utf-8")))

    def event(self, event_id: str) -> CalendarEvent | None:
        return next((e for e in self.events if e.id == event_id), None)

    def active_at(self, now: datetime) -> list[tuple[CalendarEvent, Occurrence]]:
        """Events live at ``now`` (the occurrence interval contains ``now``)."""
        out: list[tuple[CalendarEvent, Occurrence]] = []
        for ev in self.events:
            for occ in ev.occurrences_in(now, now + timedelta(seconds=1)):
                if occ.contains(now):
                    out.append((ev, occ))
                    break
        return out

    def upcoming(
        self, now: datetime, horizon_days: float = 3.0
    ) -> list[tuple[CalendarEvent, Occurrence]]:
        """Events whose next start falls in ``(now, now + horizon]``, soonest first.

        Excludes events already live at ``now`` (those are :meth:`active_at`).
        One row per event — its nearest upcoming occurrence.
        """
        w_end = now + timedelta(days=horizon_days)
        active_ids = {ev.id for ev, _ in self.active_at(now)}
        out: list[tuple[CalendarEvent, Occurrence]] = []
        for ev in self.events:
            if ev.id in active_ids:
                continue  # live right now → reported by active_at, not "upcoming"
            nxt = next(
                (
                    occ
                    for occ in ev.occurrences_in(now, w_end)
                    if occ.start > now
                ),
                None,
            )
            if nxt is not None:
                out.append((ev, nxt))
        out.sort(key=lambda pair: pair[1].start)
        return out

    def digest(self, now: datetime, days: int = 3) -> list[dict[str, Any]]:
        """Per-day breakdown for today + the next ``days - 1`` server-days.

        This is the "what's on today and the next couple of days" view the bot
        uses to plan. Each bucket lists the events occurring that day with their
        UTC start/end and whether they're live as of ``now``.
        """
        buckets: list[dict[str, Any]] = []
        today = datetime(now.year, now.month, now.day, tzinfo=UTC)
        for i in range(max(1, days)):
            day_start = today + timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            rows: list[dict[str, Any]] = [
                {
                    "id": ev.id,
                    "title": ev.title,
                    "category": ev.category,
                    "scenario": ev.scenario,
                    "strategy": ev.strategy,
                    "start": occ.start.isoformat(),
                    "end": occ.end.isoformat(),
                    "active_now": occ.contains(now),
                }
                for ev in self.events
                for occ in ev.occurrences_in(day_start, day_end)
            ]
            rows.sort(key=lambda r: r["start"])
            buckets.append({"date": day_start.date().isoformat(), "events": rows})
        return buckets

    def state_flags(self, now: datetime) -> dict[str, int]:
        """Flat ``flag -> 1|0`` map for every event carrying a ``state_flag``.

        Written verbatim into player state so other modules' ``active_when``
        conditions (e.g. the stamina allocator) can gate on a live event without
        re-deriving the schedule. Inactive flags are emitted as ``0`` so a
        condition reads deterministically false rather than "missing".
        """
        active_ids = {ev.id for ev, _ in self.active_at(now)}
        return {
            ev.state_flag: (1 if ev.id in active_ids else 0)
            for ev in self.events
            if ev.state_flag
        }
