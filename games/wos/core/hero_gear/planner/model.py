"""Static Hero Gear multi-track ladders.

Combines two files: the planner config ``games/wos/db/hero_gear.yaml`` (6 pieces +
per-track resource/gate/source) and the sheet-sourced ``db/gear/enhancement.yaml``
(the single source of truth for the numeric per-level costs). Three tracks — enhance
(1..100), mastery (1..20), widget (1..10) — each a flat ordinal ladder of single-
material steps. Pure, lru-cached. Feeds :mod:`planner`; live readers deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/hero_gear/planner/ → parents[3] = games/wos, parents[5] = repo root
_HERE = Path(__file__).resolve()
DEFAULT_CONFIG_PATH = _HERE.parents[3] / "db" / "hero_gear.yaml"
DEFAULT_ENHANCEMENT_PATH = _HERE.parents[5] / "db" / "gear" / "enhancement.yaml"


@dataclass(frozen=True, slots=True)
class TrackLadder:
    """One upgrade track: a flat ordinal ladder of single-material steps."""

    name: str                            # enhance | mastery | widget
    resource: str                        # enhancement_xp | essence_stones | weapon_widget
    unlock_furnace_level: int
    max_level: int
    levels: Mapping[int, int]            # ordinal → material cost of that step

    def cost_at(self, level: int) -> int | None:
        return self.levels.get(int(level))


@dataclass(frozen=True, slots=True)
class HeroGearData:
    """The 6-piece catalog + the 3 upgrade tracks."""

    pieces: Mapping[str, str]            # piece_id → troop_type (6 pieces)
    tracks: Mapping[str, TrackLadder]    # track name → ladder


def _int_keyed(raw: object) -> dict[int, int]:
    """``{1: 10, 2: 15, …}`` from a yaml mapping, dropping non-int keys (e.g. ``total``)."""
    out: dict[int, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return out


@lru_cache(maxsize=2)
def load_hero_gear_data(
    config_path: str | Path | None = None, enhancement_path: str | Path | None = None
) -> HeroGearData:
    """Parse the config + the enhancement sheet into a :class:`HeroGearData`."""
    cfg = yaml.safe_load(Path(config_path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")) or {}
    enh = yaml.safe_load(
        Path(enhancement_path or DEFAULT_ENHANCEMENT_PATH).read_text(encoding="utf-8")
    ) or {}

    pieces = {
        str(p["id"]): str(p.get("troop_type", ""))
        for p in (cfg.get("pieces") or [])
        if isinstance(p, dict) and p.get("id")
    }

    tracks: dict[str, TrackLadder] = {}
    for name, tc in (cfg.get("tracks") or {}).items():
        tc = tc or {}
        source = str(tc.get("source", ""))
        raw = enh.get(source) or {}
        if name == "enhance":                          # nested by tier → take the configured column
            raw = raw.get(str(tc.get("source_tier", "gold"))) or {}
            levels = _int_keyed(raw)
        elif tc.get("source_field"):                   # rows are dicts → take one field
            field = str(tc["source_field"])
            levels = {int(k): int((v or {}).get(field, 0))
                      for k, v in raw.items() if str(k).isdigit()}
        else:
            levels = _int_keyed(raw)
        if not levels:
            continue
        tracks[str(name)] = TrackLadder(
            name=str(name),
            resource=str(tc.get("resource", name)),
            unlock_furnace_level=int(tc.get("unlock_furnace_level", 0)),
            max_level=max(levels),
            levels=levels,
        )

    return HeroGearData(pieces=pieces, tracks=tracks)
