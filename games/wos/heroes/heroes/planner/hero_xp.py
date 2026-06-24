"""Hero level XP ladder — the cost (Hero XP) + Furnace gate to level a hero 1..80.

Loads ``games/wos/db/hero_xp.yaml`` (per-level Hero XP, shared across all heroes).
Pure, lru-cached — same shape as the troop/gear loaders. Gives the level component
of the hero-upgrade roadmap and gates the planner's LEVEL_UP step by the Furnace.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/heroes/heroes/planner/hero_xp.py → parents[3] = games/wos
DEFAULT_HERO_XP_PATH = Path(__file__).resolve().parents[3] / "db" / "hero_xp.yaml"

MAX_HERO_LEVEL = 80


@dataclass(frozen=True, slots=True)
class HeroXpLevel:
    """One hero level: the XP to reach it + the Furnace level that gates it."""

    level: int
    xp: int                          # Hero XP from the previous level to this one
    furnace: int                     # Furnace building level required


@lru_cache(maxsize=2)
def load_hero_xp(path: str | Path | None = None) -> dict[int, HeroXpLevel]:
    """Parse the XP ladder → ``level → HeroXpLevel``."""
    p = Path(path) if path else DEFAULT_HERO_XP_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[int, HeroXpLevel] = {}
    for row in doc.get("levels") or []:
        if not isinstance(row, dict) or "level" not in row:
            continue
        n = int(row["level"])
        out[n] = HeroXpLevel(level=n, xp=int(row.get("xp", 0)), furnace=int(row.get("furnace", 0)))
    return out


def level_cost(
    from_level: int, to_level: int, *, table: Mapping[int, HeroXpLevel] | None = None
) -> int:
    """Total Hero XP to go from ``from_level`` up to ``to_level`` (0 if not advancing)."""
    tbl = table if table is not None else load_hero_xp()
    return sum(
        lv.xp for n in range(int(from_level) + 1, int(to_level) + 1)
        if (lv := tbl.get(n)) is not None
    )


def level_furnace_gate(level: int, *, table: Mapping[int, HeroXpLevel] | None = None) -> int:
    """Furnace level required to reach hero ``level`` (0 if the level is off the table)."""
    tbl = table if table is not None else load_hero_xp()
    lv = tbl.get(int(level))
    return lv.furnace if lv is not None else 0
