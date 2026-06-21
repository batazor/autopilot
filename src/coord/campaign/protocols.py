"""Typing seams the pure planner depends on — abstractions, not Redis/games.

The WoS adapter implements these over live state (player-state flags, the fleet
registry, the calendar layer); tests implement them with plain dicts. Keeping the
planner behind these Protocols is what lets it stay pure and game-agnostic.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FleetSnapshot(Protocol):
    def online(self, fid: str) -> bool:
        """Is the account currently active + online somewhere in the fleet?"""
        ...

    def signal(self, fid: str, name: str) -> bool:
        """Is a per-participant barrier signal (e.g. ``city_empty``) set for this account?"""
        ...


@runtime_checkable
class CalendarView(Protocol):
    def window_active(self, slug: str) -> bool:
        """Is the named event window open right now?"""
        ...

    def ends_in_s(self, slug: str) -> float:
        """Seconds until the window closes (``inf`` if not open / unknown)."""
        ...
