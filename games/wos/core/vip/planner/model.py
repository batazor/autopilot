"""Static VIP level ladder parsed from ``games/wos/db/vip_levels.yaml``.

Pure parsing + lookups, lru-cached (same shape as the charm/pet loaders). VIP is
a single linear track (VIP 1 → 12): one cumulative-XP ladder, no slots. VIP Points
items apply 1:1 as VIP XP, so the planner costs each level-up in ``vip_points`` and
decomposes a target into the item denominations. Feeds :mod:`planner`.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/vip/planner/ → parents[3] = games/wos
DEFAULT_VIP_LEVELS_PATH = Path(__file__).resolve().parents[3] / "db" / "vip_levels.yaml"


@dataclass(frozen=True, slots=True)
class VipLevel:
    """One VIP level: total XP to reach it + XP to the next level."""

    level: int
    cumulative_xp: int           # total VIP XP to BE at this level (VIP 1 = 0 base)
    xp_to_next: int | None       # VIP XP from this level to the next (None at the cap)
    power: int | None = None     # reserved (the source has no per-level power)


@dataclass(frozen=True, slots=True)
class VipData:
    """The VIP ladder + the VIP Points item denominations."""

    levels: Mapping[int, VipLevel]       # level → VipLevel
    point_items: tuple[int, ...]         # VIP Points denominations (e.g. 10/100/1k/10k)
    unlock_furnace_level: int            # Furnace gate (0 = none)
    max_level: int

    def level(self, n: int) -> VipLevel | None:
        return self.levels.get(int(n))

    def cumulative_xp(self, n: int) -> int:
        """Total VIP XP to be at level ``n`` (clamped to the table's range)."""
        n = int(n)
        lv = self.levels.get(n)
        if lv is not None:
            return lv.cumulative_xp
        if not self.levels:
            return 0
        lo, hi = min(self.levels), max(self.levels)
        return self.levels[lo].cumulative_xp if n < lo else self.levels[hi].cumulative_xp

    def xp_to_next(self, n: int) -> int | None:
        """VIP XP to go from level ``n`` to ``n + 1`` (None at/after the cap)."""
        lv = self.levels.get(int(n))
        return lv.xp_to_next if lv is not None else None


@lru_cache(maxsize=2)
def load_vip_levels(path: str | Path | None = None) -> VipData:
    """Parse ``vip_levels.yaml`` into a :class:`VipData`."""
    p = Path(path) if path else DEFAULT_VIP_LEVELS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    levels: dict[int, VipLevel] = {}
    for row in doc.get("levels") or []:
        if not isinstance(row, dict) or "level" not in row:
            continue
        n = int(row["level"])
        xtn = row.get("xp_to_next")
        power = row.get("power")
        levels[n] = VipLevel(
            level=n,
            cumulative_xp=int(row.get("cumulative_xp", 0)),
            xp_to_next=int(xtn) if isinstance(xtn, (int, float)) else None,
            power=int(power) if isinstance(power, (int, float)) else None,
        )

    point_items = tuple(int(x) for x in (doc.get("point_items") or (10, 100, 1000, 10000)))

    return VipData(
        levels=levels,
        point_items=point_items,
        unlock_furnace_level=int(doc.get("unlock_furnace_level", 0)),
        max_level=int(doc.get("max_level", max(levels, default=12))),
    )
