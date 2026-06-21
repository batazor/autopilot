"""Fire-Crystal building upgrade costs — exact per-sublevel named-resource tables.

Loads ``games/wos/db/fire_crystal_costs.yaml`` (exact integer FC costs re-encoded
from public game data; our ``db/buildings`` furnace.yaml carries only approximate
item-icon amounts). Gives the FC cost component — in canonical resource names
(meat/wood/coal/iron/fire_crystal/refined_fire_crystal) — for a cost/ROI model.
Pure. The three troop camps share the ``camp`` table.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# games/wos/core/building/fc_costs.py → parents[2] = games/wos
DEFAULT_FC_COSTS_PATH = Path(__file__).resolve().parents[2] / "db" / "fire_crystal_costs.yaml"

# Troop camps share one cost table.
_ALIASES = {"infantry_camp": "camp", "lancer_camp": "camp", "marksman_camp": "camp"}


@dataclass(frozen=True, slots=True)
class FcLevel:
    """One Fire-Crystal sublevel's upgrade cost."""

    id: str              # "fc1_0", "fc1_1", …
    label: str           # "FC 1", "FC 1-1", …
    tier: str            # "FC 1" … "FC 10"
    cost: Mapping[str, int]


@lru_cache(maxsize=2)
def load_fc_costs(path: str | Path | None = None) -> dict[str, tuple[FcLevel, ...]]:
    """Load the FC cost tables: ``building id → ordered FcLevels``."""
    p = Path(path) if path else DEFAULT_FC_COSTS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, tuple[FcLevel, ...]] = {}
    for name, levels in (doc.get("buildings") or {}).items():
        out[str(name)] = tuple(
            FcLevel(
                id=str(lvl["id"]), label=str(lvl["label"]), tier=str(lvl["tier"]),
                cost={str(k): int(v) for k, v in (lvl.get("cost") or {}).items()},
            )
            for lvl in levels
        )
    return out


def levels_for(
    building: str, costs: Mapping[str, Sequence[FcLevel]] | None = None
) -> tuple[FcLevel, ...]:
    """FC levels for a building (resolving the shared ``camp`` table for the camps)."""
    table = costs if costs is not None else load_fc_costs()
    return tuple(table.get(_ALIASES.get(building, building), ()))


def cumulative_cost(
    building: str,
    from_id: str | None,
    to_id: str,
    costs: Mapping[str, Sequence[FcLevel]] | None = None,
) -> dict[str, int]:
    """Total named-resource cost to upgrade from *after* ``from_id`` up to ``to_id``
    (inclusive) — the cumulative sum the in-game upgrade screen shows.

    ``from_id=None`` starts from the bottom of the table. Raises ``KeyError`` if an
    id isn't in the building's table.
    """
    levels = levels_for(building, costs)
    ids = [lv.id for lv in levels]
    if to_id not in ids:
        msg = f"fc_costs: level {to_id!r} not in {building!r}"
        raise KeyError(msg)
    to_i = ids.index(to_id)
    from_i = ids.index(from_id) if (from_id is not None and from_id in ids) else -1
    total: dict[str, int] = {}
    for lv in levels[from_i + 1 : to_i + 1]:
        for resource, amount in lv.cost.items():
            total[resource] = total.get(resource, 0) + amount
    return total
