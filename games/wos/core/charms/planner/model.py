"""Static Chief Charms upgrade table parsed from ``games/wos/db/chief_charms.yaml``.

Pure parsing + lookups, lru-cached (same shape as the pet/troop loaders). One
shared per-level cost/power table drives all 18 charm slots — 6 Chief Gear pieces ×
3 charms, with ``slots_per_type`` slots per troop type (only the stat's troop-type
differs, never the cost). Feeds :mod:`planner`, which decides which charm to raise
next; live readers (per-slot levels) are deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/charms/planner/ → parents[3] = games/wos
DEFAULT_CHARMS_PATH = Path(__file__).resolve().parents[3] / "db" / "chief_charms.yaml"


@dataclass(frozen=True, slots=True)
class CharmLevel:
    """One charm level: the materials to reach it + the charm's power there."""

    level: int
    cost: Mapping[str, int]          # charm_guide / charm_design / charm_secrets
    power: int | None                # power AT this level (None where the source lacks it)


@dataclass(frozen=True, slots=True)
class CharmData:
    """The shared upgrade table + the slot catalog."""

    levels: Mapping[int, CharmLevel]     # level → CharmLevel
    slots: Mapping[str, str]             # slot_id → troop_type (18 slots)
    unlock_furnace_level: int
    max_level: int

    def level(self, n: int) -> CharmLevel | None:
        return self.levels.get(int(n))


@lru_cache(maxsize=2)
def load_charm_data(path: str | Path | None = None) -> CharmData:
    """Parse ``chief_charms.yaml`` into a :class:`CharmData`."""
    p = Path(path) if path else DEFAULT_CHARMS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    levels: dict[int, CharmLevel] = {}
    for row in doc.get("levels") or []:
        if not isinstance(row, dict) or "level" not in row:
            continue
        n = int(row["level"])
        power = row.get("power")
        levels[n] = CharmLevel(
            level=n,
            cost={str(k): int(v) for k, v in (row.get("cost") or {}).items()},
            power=int(power) if isinstance(power, (int, float)) else None,
        )

    types = [str(t) for t in (doc.get("troop_types") or ("infantry", "lancer", "marksman"))]
    per_type = int(doc.get("slots_per_type", 6))
    slots = {f"{t}_{i}": t for t in types for i in range(1, per_type + 1)}

    return CharmData(
        levels=levels,
        slots=slots,
        unlock_furnace_level=int(doc.get("unlock_furnace_level", 25)),
        max_level=int(doc.get("max_level", max(levels, default=16))),
    )
