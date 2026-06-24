"""Static Chief Gear upgrade ladder parsed from ``games/wos/db/chief_gear.yaml``.

Pure parsing + lookups, lru-cached (sibling of the charm loader). One shared
tier+star ladder (Green → Pink, 42 ordinal steps) drives all 6 gear pieces — only
the stat's troop-type differs per piece, never the cost. Feeds :mod:`planner`,
which decides which piece to upgrade next; live readers (per-piece level) deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/gear/planner/ → parents[3] = games/wos
DEFAULT_GEAR_PATH = Path(__file__).resolve().parents[3] / "db" / "chief_gear.yaml"


@dataclass(frozen=True, slots=True)
class GearLevel:
    """One ladder step: the materials to reach it, its tier+star label, its power."""

    level: int                       # ordinal (1 = Green 0)
    label: str                       # in-game tier+star ("green_0" … "pink_t3_4")
    cost: Mapping[str, int]          # hardened_alloy / polishing_solution / design_plans / lunar_amber
    power: int | None                # piece power AT this step


@dataclass(frozen=True, slots=True)
class GearData:
    """The shared upgrade ladder + the 6-piece catalog."""

    levels: Mapping[int, GearLevel]      # ordinal → GearLevel
    slots: Mapping[str, str]             # piece_id → troop_type (6 pieces)
    unlock_furnace_level: int
    max_level: int

    def level(self, n: int) -> GearLevel | None:
        return self.levels.get(int(n))


@lru_cache(maxsize=2)
def load_gear_data(path: str | Path | None = None) -> GearData:
    """Parse ``chief_gear.yaml`` into a :class:`GearData`."""
    p = Path(path) if path else DEFAULT_GEAR_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    levels: dict[int, GearLevel] = {}
    for row in doc.get("levels") or []:
        if not isinstance(row, dict) or "level" not in row:
            continue
        n = int(row["level"])
        power = row.get("power")
        levels[n] = GearLevel(
            level=n,
            label=str(row.get("label", n)),
            cost={str(k): int(v) for k, v in (row.get("cost") or {}).items()},
            power=int(power) if isinstance(power, (int, float)) else None,
        )

    slots = {
        str(p["id"]): str(p.get("troop_type", ""))
        for p in (doc.get("pieces") or [])
        if isinstance(p, dict) and p.get("id")
    }

    return GearData(
        levels=levels,
        slots=slots,
        unlock_furnace_level=int(doc.get("unlock_furnace_level", 22)),
        max_level=int(doc.get("max_level", max(levels, default=42))),
    )
