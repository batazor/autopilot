"""Date-anchored seasonal/festival events — the fixed yearly calendar.

Complements the live in-game calendar reader: those flags say what's running *now*;
this is the FIXED, date-tied schedule (New Year, Valentines, Anniversary, Tundra
Games, Halloween…) parsed from ``games/wos/db/seasonal_events.yaml``. It lets the
bot ANTICIPATE festival windows by date — expect themed deals / login rewards /
packs — before the in-game calendar surfaces them.

Pure: load the catalog, ask what's active on a given month/day, or what's coming up.
Windows are approximate week-of-month ranges; movable feasts are flagged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# games/wos/core/calendar/seasonal.py → parents[2] = games/wos
DEFAULT_SEASONAL_PATH = Path(__file__).resolve().parents[2] / "db" / "seasonal_events.yaml"

# Cumulative days before each month (non-leap) → day-of-year math for "days until".
_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
_YEAR_DAYS = 365


def _day_of_year(month: int, day: int) -> int:
    if not 1 <= month <= 12:
        return 0
    return _CUM_DAYS[month - 1] + day


@dataclass(frozen=True, slots=True)
class Window:
    month: int
    start_day: int
    end_day: int

    def contains(self, month: int, day: int) -> bool:
        return month == self.month and self.start_day <= day <= self.end_day

    @property
    def start_doy(self) -> int:
        return _day_of_year(self.month, self.start_day)


@dataclass(frozen=True, slots=True)
class SeasonalEvent:
    id: str
    name: str
    category: str                     # festival | activity
    windows: tuple[Window, ...]
    approximate: bool = False         # movable feast (lunar/computed) — re-confirm yearly
    note: str = ""

    def active_on(self, month: int, day: int) -> bool:
        return any(w.contains(month, day) for w in self.windows)


def load_seasonal_events(path: str | Path | None = None) -> dict[str, SeasonalEvent]:
    """Parse the yearly seasonal calendar into ``id → SeasonalEvent``."""
    p = Path(path) if path else DEFAULT_SEASONAL_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, SeasonalEvent] = {}
    for raw in doc.get("events") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        windows = tuple(
            Window(int(w["month"]), int(w["start_day"]), int(w["end_day"]))
            for w in (raw.get("windows") or [])
            if isinstance(w, dict)
        )
        out[str(raw["id"])] = SeasonalEvent(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            category=str(raw.get("category") or "festival"),
            windows=windows,
            approximate=bool(raw.get("approximate", False)),
            note=str(raw.get("note") or ""),
        )
    return out


def events_active_on(
    catalog: Mapping[str, SeasonalEvent], month: int, day: int
) -> list[SeasonalEvent]:
    """Seasonal events whose window covers ``month``/``day``."""
    return [e for e in catalog.values() if e.active_on(month, day)]


def upcoming(
    catalog: Mapping[str, SeasonalEvent],
    month: int,
    day: int,
    *,
    within_days: int = 14,
) -> list[tuple[SeasonalEvent, int]]:
    """Events starting within ``within_days`` (and not already active), soonest first.

    Days-until is approximate (non-leap day-of-year, wraps the year end) — enough to
    anticipate a window and pre-position (hoard, expect deals).
    """
    today = _day_of_year(month, day)
    out: list[tuple[SeasonalEvent, int]] = []
    for event in catalog.values():
        if event.active_on(month, day):
            continue
        starts = [(w.start_doy - today) % _YEAR_DAYS for w in event.windows]
        future = [d for d in starts if 0 < d <= within_days]
        if future:
            out.append((event, min(future)))
    return sorted(out, key=lambda pair: (pair[1], pair[0].id))


def active_categories(
    catalog: Mapping[str, SeasonalEvent], month: int, day: int
) -> set[str]:
    """Categories live now — e.g. {"activity"} during Tundra Games (a points event)."""
    return {e.category for e in events_active_on(catalog, month, day)}
