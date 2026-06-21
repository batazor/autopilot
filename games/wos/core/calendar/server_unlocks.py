"""Server-age unlock schedule — derive what's open and anticipate what's next.

Whiteout Survival opens content by *server age*: Hero Hall generations, pet
generations, and play modes (Red equipment, Huojing, War Academy, Chiyan tech …)
each unlock at a fixed number of days after the server opens. Those timings are
config (per server type) in ``games/wos/db/server_unlocks.yaml``; this reads a
named profile and answers two strategy questions:

* **What's the current tier?** ``hero_generation_at(days)`` is exactly the
  ``current_generation`` the hero planner needs (it down-weights older-gen heroes),
  and ``pet_generation_at(days)`` the analogous pet tier — both derived from server
  age instead of hand-entered.
* **What's coming?** ``upcoming(days, within_days)`` lists unlocks just over the
  horizon so the bot can pre-position (hoard recruits before a new hero generation,
  save gear mats before Red equipment opens).

Pure: profile + server-age days in, plain values / dataclasses out.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/calendar/server_unlocks.py → parents[2] = games/wos
DEFAULT_UNLOCKS_PATH = Path(__file__).resolve().parents[2] / "db" / "server_unlocks.yaml"

# Hero Hall ships generations 1-8 open from day 0; pets start with none.
DEFAULT_BASE_HERO_GENERATION = 8
DEFAULT_BASE_PET_GENERATION = 0


@dataclass(frozen=True, slots=True)
class UnlockEvent:
    """One feature's unlock, relative to a moment in the server's life."""

    kind: str            # "hero_generation" | "pet_generation" | "mode"
    key: str             # "gen_10" | "pet_gen_3" | "red_equipment"
    unlock_day: int      # server age (days) it opens at
    days_until: int      # days from the queried age (>0 = upcoming)


@dataclass(frozen=True, slots=True)
class UnlockSchedule:
    """A server profile's age→unlock timings (from one ``profiles:`` entry)."""

    profile: str
    hero_generations: Mapping[int, int]    # generation → unlock day
    pet_generations: Mapping[int, int]     # generation → unlock day
    modes: Mapping[str, int]               # mode key → unlock day
    base_hero_generation: int = DEFAULT_BASE_HERO_GENERATION
    base_pet_generation: int = DEFAULT_BASE_PET_GENERATION

    def hero_generation_at(self, days: int | None) -> int:
        """Highest Hero Hall generation recruitable at server age ``days`` (the
        hero planner's ``current_generation``). ``None`` → the day-0 base."""
        gen = self.base_hero_generation
        if days is None:
            return gen
        for g, unlock in self.hero_generations.items():
            if days >= unlock:
                gen = max(gen, g)
        return gen

    def pet_generation_at(self, days: int | None) -> int:
        """Highest pet generation unlocked at server age ``days``."""
        gen = self.base_pet_generation
        if days is None:
            return gen
        for g, unlock in self.pet_generations.items():
            if days >= unlock:
                gen = max(gen, g)
        return gen

    def unlocked_modes(self, days: int | None) -> dict[str, int]:
        """``mode key → unlock day`` for modes already open at ``days``."""
        if days is None:
            return {}
        return {k: d for k, d in self.modes.items() if days >= d}

    def upcoming(self, days: int | None, *, within_days: int = 30) -> list[UnlockEvent]:
        """Everything unlocking in ``(days, days + within_days]``, soonest first.

        Spans all three kinds (hero generations, pet generations, modes) so one
        call answers "what should I pre-position for". ``days=None`` → empty.
        """
        if days is None:
            return []
        sources: dict[str, dict[str, int]] = {
            "hero_generation": {f"gen_{g}": d for g, d in self.hero_generations.items()},
            "pet_generation": {f"pet_gen_{g}": d for g, d in self.pet_generations.items()},
            "mode": dict(self.modes),
        }
        out: list[UnlockEvent] = []
        for kind, table in sources.items():
            for key, unlock in table.items():
                if days < unlock <= days + within_days:
                    out.append(UnlockEvent(kind, key, int(unlock), int(unlock - days)))
        return sorted(out, key=lambda e: (e.days_until, e.kind, e.key))


def load_unlock_schedule(
    profile: str = "beta", path: str | Path | None = None
) -> UnlockSchedule:
    """Load one server profile from ``server_unlocks.yaml`` (default: ``beta``)."""
    p = Path(path) if path else DEFAULT_UNLOCKS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    profiles = doc.get("profiles") or {}
    raw = profiles.get(profile)
    if not isinstance(raw, dict):
        msg = f"server_unlocks: profile {profile!r} not found"
        raise KeyError(msg)

    def _int_keyed(section: str) -> dict[int, int]:
        block = raw.get(section) or {}
        return {int(k): int(v) for k, v in block.items()} if isinstance(block, dict) else {}

    modes_block = raw.get("modes") or {}
    modes = {str(k): int(v) for k, v in modes_block.items()} if isinstance(modes_block, dict) else {}

    return UnlockSchedule(
        profile=str(profile),
        hero_generations=_int_keyed("hero_generations"),
        pet_generations=_int_keyed("pet_generations"),
        modes=modes,
        base_hero_generation=int(raw.get("base_hero_generation", DEFAULT_BASE_HERO_GENERATION)),
        base_pet_generation=int(raw.get("base_pet_generation", DEFAULT_BASE_PET_GENERATION)),
    )
