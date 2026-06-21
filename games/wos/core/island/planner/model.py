"""Static Daybreak Island data parsed from ``games/wos/db/island/*.yaml``.

No Redis, no ADB, no game IO — pure parsing + lookups, unit testable. The data
feeds :mod:`planner`, which decides *which island thing to build/upgrade next*.

The island is a self-contained economy that mirrors the main city: the **Tree of
Life** is its Furnace (the spearhead), but instead of a prerequisite *building*,
each Tree-of-Life level is gated by a **Prosperity** threshold plus a one-time
**Life Essence** cost. Prosperity is produced by **decorations** (which also carry
the permanent main-game buffs), so advancing the tree is a loop: raise the tree →
more Life Essence/hour → afford decorations → more Prosperity → next tree level.

This module just parses and exposes that data; the policy + planner encode the
loop. Live readers (current tree level, prosperity, LE balance, owned decoration
levels) are deferred — the bot can't read island state yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# games/wos/core/island/planner/ → parents[3] = games/wos
DEFAULT_ISLAND_DIR = Path(__file__).resolve().parents[3] / "db" / "island"


@dataclass(frozen=True, slots=True)
class StatBonus:
    """A permanent stat bonus granted by a tree level or structure."""

    stat: str
    amount: float
    unit: str = "percent"


@dataclass(frozen=True, slots=True)
class TreeLevel:
    """One Tree-of-Life level: what it costs to reach and what it returns."""

    level: int
    prosperity_required: int
    life_essence: int
    le_per_hour: int
    bonus: StatBonus | None


@dataclass(frozen=True, slots=True)
class Decoration:
    """A buff-bearing (or prosperity-only) island decoration."""

    id: str
    name: str
    rarity: str               # rare | epic | mythic | common | uncommon
    life_essence: int         # build cost
    max_level: int
    prosperity: int           # prosperity contributed (at max level; see yaml note)
    kind: str                 # semantic buff kind → role category in policy
    stat: str | None = None
    amount: float = 0.0


@dataclass(frozen=True, slots=True)
class Structure:
    """A non-decoration structure (LE producer or the Starry Lighthouse)."""

    id: str
    name: str
    role: str                              # producer | buff
    max_level: int | None = None
    plots: int = 1
    unlock: Mapping[str, Any] | None = None
    life_essence: int = 0                  # blueprint / build cost where applicable
    prosperity: int = 0
    bonuses: tuple[StatBonus, ...] = ()


@dataclass(frozen=True, slots=True)
class IslandData:
    """The whole parsed island: tree ladder + decorations + structures."""

    tree: tuple[TreeLevel, ...]
    decorations: tuple[Decoration, ...]
    fillers: tuple[Decoration, ...]
    structures: tuple[Structure, ...]

    def tree_level(self, level: int) -> TreeLevel | None:
        for t in self.tree:
            if t.level == level:
                return t
        return None

    def tree_max(self) -> int:
        return max((t.level for t in self.tree), default=0)

    def decoration(self, deco_id: str) -> Decoration | None:
        for d in self.decorations:
            if d.id == deco_id:
                return d
        return None

    def structure(self, structure_id: str) -> Structure | None:
        for s in self.structures:
            if s.id == structure_id:
                return s
        return None


def _bonus(raw: Mapping[str, Any] | None) -> StatBonus | None:
    if not isinstance(raw, dict) or "stat" not in raw:
        return None
    return StatBonus(
        stat=str(raw["stat"]),
        amount=float(raw.get("amount", 0.0)),
        unit=str(raw.get("unit", "percent")),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_island_data(island_dir: str | Path | None = None) -> IslandData:
    """Load + parse the three island yaml files into one :class:`IslandData`."""
    base = Path(island_dir) if island_dir else DEFAULT_ISLAND_DIR

    tree_doc = _load_yaml(base / "tree_of_life.yaml")
    tree = tuple(
        TreeLevel(
            level=int(row["level"]),
            prosperity_required=int(row.get("prosperity_required", 0)),
            life_essence=int(row.get("life_essence", 0)),
            le_per_hour=int(row.get("le_per_hour", 0)),
            bonus=_bonus(row.get("bonus")),
        )
        for row in (tree_doc.get("levels") or [])
    )

    deco_doc = _load_yaml(base / "decorations.yaml")
    decorations = tuple(_parse_decoration(row) for row in (deco_doc.get("decorations") or []))
    fillers = tuple(_parse_decoration(row) for row in (deco_doc.get("prosperity_fillers") or []))

    struct_doc = _load_yaml(base / "structures.yaml")
    structures = tuple(_parse_structure(row) for row in (struct_doc.get("structures") or []))

    return IslandData(tree=tree, decorations=decorations, fillers=fillers, structures=structures)


def _parse_decoration(row: Mapping[str, Any]) -> Decoration:
    return Decoration(
        id=str(row["id"]),
        name=str(row.get("name", row["id"])),
        rarity=str(row.get("rarity", "")),
        life_essence=int(row.get("life_essence", 0)),
        max_level=int(row.get("max_level", 1)),
        prosperity=int(row.get("prosperity", 0)),
        kind=str(row.get("kind", "")),
        stat=(str(row["stat"]) if row.get("stat") else None),
        amount=float(row.get("amount", 0.0)),
    )


def _parse_structure(row: Mapping[str, Any]) -> Structure:
    bonuses: Sequence[Any] = row.get("bonuses") or []
    return Structure(
        id=str(row["id"]),
        name=str(row.get("name", row["id"])),
        role=str(row.get("role", "")),
        max_level=(int(row["max_level"]) if row.get("max_level") is not None else None),
        plots=int(row.get("plots", 1)),
        unlock=row.get("unlock"),
        life_essence=int(row.get("blueprint_life_essence", row.get("life_essence", 0)) or 0),
        prosperity=int(row.get("prosperity", 0)),
        bonuses=tuple(b for b in (_bonus(x) for x in bonuses) if b is not None),
    )
