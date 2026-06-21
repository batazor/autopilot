"""Troop combat stats — per troop type, camp tier, and Fire-Crystal level.

Loads ``games/wos/db/troops.yaml`` (per-unit power/attack/defense/lethality/
health + gathering ``load``, re-encoded game facts). The allocator costs a march
in *typed troop counts*; this turns a count + the troops' tier/FC into power and
carry capacity — the missing combat/gather weight for a march's value. Pure.

Lookup is keyed ``(type, tier, fc)``; ``type`` is ``infantry``/``lancer``/
``marksman``, ``tier`` 1-11, ``fc`` 0-10. Base march ``speed`` is a single
constant for every row (see ``SPEED``).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

# games/wos/core/resources/troop_stats.py → parents[2] = games/wos
DEFAULT_TROOPS_PATH = Path(__file__).resolve().parents[2] / "db" / "troops.yaml"

TROOP_TYPES = ("infantry", "lancer", "marksman")


@dataclass(frozen=True, slots=True)
class TroopStat:
    """One troop's per-unit stats at a given camp tier and Fire-Crystal level."""

    type: str         # "infantry" | "lancer" | "marksman"
    tier: int         # camp troop level, 1-11
    fc: int           # Fire-Crystal level, 0-10
    name: str         # tier name ("Rookie" … "Helios")
    power: int        # per-unit power
    attack: int
    defense: int
    lethality: int
    health: int
    load: int         # gathering carry capacity (tier-dependent)


@lru_cache(maxsize=2)
def _load(path: str) -> tuple[dict[tuple[str, int, int], TroopStat], int]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    speed = int(doc.get("speed", 0))
    table: dict[tuple[str, int, int], TroopStat] = {}
    for ttype, rows in (doc.get("troops") or {}).items():
        for r in rows:
            stat = TroopStat(
                type=str(ttype),
                tier=int(r["tier"]),
                fc=int(r["fc"]),
                name=str(r["name"]),
                power=int(r["power"]),
                attack=int(r["attack"]),
                defense=int(r["defense"]),
                lethality=int(r["lethality"]),
                health=int(r["health"]),
                load=int(r["load"]),
            )
            table[(stat.type, stat.tier, stat.fc)] = stat
    return table, speed


def load_troop_stats(path: str | Path | None = None) -> dict[tuple[str, int, int], TroopStat]:
    """All troop stats keyed by ``(type, tier, fc)``."""
    return _load(str(path or DEFAULT_TROOPS_PATH))[0]


def base_speed(path: str | Path | None = None) -> int:
    """Per-unit base march speed (constant across every row)."""
    return _load(str(path or DEFAULT_TROOPS_PATH))[1]


def troop_stat(
    troop_type: str, tier: int, fc: int = 0, path: str | Path | None = None
) -> TroopStat:
    """Look up one troop's stats. Raises ``KeyError`` if the combo is unknown."""
    return load_troop_stats(path)[(troop_type, int(tier), int(fc))]
