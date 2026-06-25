"""Sunfire Castle territory — fixed global-map structures + buff towers + zones.

Loads ``games/wos/db/sunfire_castle_territory.yaml`` (game facts re-encoded from the
wostools territory planner): the central Sunfire Castle, 4 turrets, 16 forts
(4 strongholds + 12 fortresses), 74 buff towers in 8 types, and 3 zone bands — all on
the main global/state world map's 1200×1200 tile grid where ``[col, row] = [X, Y]``.

These positions are identical on every server. The module is pure facts with no
consumer logic; it is shared by the radar territory overlay (markers / ground-truth
anchors) and the buff-tower capture planner (``tower_plan.py``). Loader pattern mirrors
``games/wos/core/koi/koi_points.py`` (``yaml.safe_load`` + ``@lru_cache``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from math import hypot
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

# games/wos/core/sunfire_castle/ → parents[2] = games/wos
DEFAULT_TERRITORY_PATH = (
    Path(__file__).resolve().parents[2] / "db" / "sunfire_castle_territory.yaml"
)

# The 8 buff-tower types, in the canonical (yaml) order.
BUFF_TYPES = (
    "construction",
    "defense",
    "tech",
    "weapon",
    "gathering",
    "production",
    "training",
    "expedition",
)


@dataclass(frozen=True, slots=True)
class Structure:
    """A fixed non-buff structure: the castle, a turret, a stronghold or a fortress."""

    kind: str  # "castle" | "turret" | "stronghold" | "fortress"
    label: str
    col: int
    row: int
    size: int = 1


@dataclass(frozen=True, slots=True)
class Tower:
    """One capturable buff tower at a fixed map coordinate.

    ``booster_pct`` is the numeric buff (e.g. ``5.0`` for ``"+5%"``); ``dist_from_castle``
    is the Euclidean tile distance to the map centre (closer ≈ easier / more contested).
    """

    tower_id: str  # deterministic: f"{buff_type}_l{level}_{index}"
    buff_type: str
    label: str  # e.g. "Research"
    bonus: str  # e.g. "Research Speed"
    color: str  # hex from the source planner
    level: int
    booster: str  # raw "+5%"
    booster_pct: float
    heavily_injured: str
    losses: str
    col: int
    row: int
    dist_from_castle: float


@dataclass(frozen=True, slots=True)
class Zone:
    """An axis-aligned territory band (inclusive bounding box in game col/row)."""

    id: str
    label: str
    min_col: int
    min_row: int
    max_col: int
    max_row: int


@dataclass(frozen=True, slots=True)
class Territory:
    """The whole Sunfire Castle territory: structures, buff towers and zone bands."""

    grid_size: int
    castle: Structure
    turrets: tuple[Structure, ...]
    strongholds: tuple[Structure, ...]
    fortresses: tuple[Structure, ...]
    towers: tuple[Tower, ...]
    zones: tuple[Zone, ...]


def _pct(raw: str) -> float:
    """Pull the leading number out of a booster string ("+5%" → 5.0, "" → 0.0)."""
    m = re.search(r"-?\d+(?:\.\d+)?", raw or "")
    return float(m.group()) if m else 0.0


@lru_cache(maxsize=2)
def load_territory(path: str | Path | None = None) -> Territory:
    """Load ``sunfire_castle_territory.yaml`` → :class:`Territory` (cached per process)."""
    p = Path(path) if path else DEFAULT_TERRITORY_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    grid = int(doc.get("grid_size", 1200))

    c = doc.get("castle") or {}
    castle = Structure(
        kind="castle",
        label=str(c.get("label", "Sunfire Castle")),
        col=int(c["col"]),
        row=int(c["row"]),
        size=int(c.get("size", 1)),
    )
    cx, cy = castle.col, castle.row

    def _structs(key: str, kind: str) -> tuple[Structure, ...]:
        return tuple(
            Structure(
                kind=kind,
                label=str(s.get("label", "")),
                col=int(s["col"]),
                row=int(s["row"]),
                size=int(s.get("size", 1)),
            )
            for s in (doc.get(key) or [])
        )

    towers: list[Tower] = []
    for buff_type, spec in (doc.get("buff_facilities") or {}).items():
        spec = spec or {}
        label = str(spec.get("label", buff_type))
        bonus = str(spec.get("bonus", ""))
        color = str(spec.get("color", ""))
        for lvl in spec.get("levels") or []:
            level = int(lvl.get("level", 0))
            booster = str(lvl.get("booster", ""))
            hi = str(lvl.get("heavily_injured", ""))
            loss = str(lvl.get("losses", ""))
            for i, coord in enumerate(lvl.get("coordinates") or []):
                col, row = int(coord[0]), int(coord[1])
                towers.append(
                    Tower(
                        tower_id=f"{buff_type}_l{level}_{i}",
                        buff_type=str(buff_type),
                        label=label,
                        bonus=bonus,
                        color=color,
                        level=level,
                        booster=booster,
                        booster_pct=_pct(booster),
                        heavily_injured=hi,
                        losses=loss,
                        col=col,
                        row=row,
                        dist_from_castle=round(hypot(col - cx, row - cy), 2),
                    )
                )

    zones = tuple(
        Zone(
            id=str(z.get("id", "")),
            label=str(z.get("note", z.get("label", ""))),
            min_col=int(z["min_col"]),
            min_row=int(z["min_row"]),
            max_col=int(z["max_col"]),
            max_row=int(z["max_row"]),
        )
        for z in (doc.get("zones") or [])
    )

    return Territory(
        grid_size=grid,
        castle=castle,
        turrets=_structs("turrets", "turret"),
        strongholds=_structs("strongholds", "stronghold"),
        fortresses=_structs("fortresses", "fortress"),
        towers=tuple(towers),
        zones=zones,
    )


def iter_structures(territory: Territory | None = None) -> Iterator[Structure]:
    """Yield every fixed structure: castle, then turrets, strongholds, fortresses."""
    t = territory or load_territory()
    yield t.castle
    yield from t.turrets
    yield from t.strongholds
    yield from t.fortresses


def iter_towers(territory: Territory | None = None) -> Iterator[Tower]:
    """Yield every buff tower."""
    return iter((territory or load_territory()).towers)


def iter_zones(territory: Territory | None = None) -> Iterator[Zone]:
    """Yield every territory zone band."""
    return iter((territory or load_territory()).zones)
