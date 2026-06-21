"""Static hero catalog parsed from ``games/wos/db/heroes/*.yaml``.

Pure parsing + lookups, no IO. Feeds :mod:`planner`, which decides *which hero to
invest the next books/shards into* — both being limited, tiered resources.

Each wiki yaml has ``rarity`` (Legendary/Epic/Rare → which book tier its skills
need), ``class`` (Infantry/Lancer/Marksmen) and ``sub_class`` (**Combat** vs
**Growth** — Growth = the gathering/expedition specialists like Cloris/Eugene that
fuel the economy), plus a ``shards`` table (per-tier shard cost for star
promotion — shards are hero-specific). Server *generation* is NOT in the static
data (heroes power-creep by generation); it's supplied as config to the planner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_shard_tiers(raw: dict[str, Any]) -> tuple[int, ...]:
    """Per-star shard cost from the wiki ``shards`` table (the ``Total`` column)."""
    rows = (raw.get("shards") or {}).get("rows") or []
    out: list[int] = []
    for row in rows:
        n = _to_int(row.get("Total")) if isinstance(row, dict) else None
        if n is not None and n > 0:
            out.append(n)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class HeroSpec:
    """One hero's static profile relevant to investment decisions."""

    id: str
    name: str
    rarity: str                      # Legendary | Epic | Rare
    hero_class: str                  # Infantry | Lancer | Marksmen
    sub_class: str                   # Combat | Growth
    shard_tiers: tuple[int, ...]     # shards to reach each successive star tier

    def shard_cost(self, current_star: int) -> int:
        """Shards to promote from ``current_star`` to the next (rarity fallback)."""
        if self.shard_tiers:
            idx = min(max(current_star, 0), len(self.shard_tiers) - 1)
            return self.shard_tiers[idx]
        return {"Legendary": 15, "Epic": 10, "Rare": 5}.get(self.rarity, 5)


def load_hero_catalog(directory: str | Path | None = None) -> dict[str, HeroSpec]:
    """Parse every hero wiki yaml into ``id → HeroSpec``."""
    if directory is not None:
        d = Path(directory)
    else:
        from config.heroes import heroes_db_dir

        d = heroes_db_dir()
    catalog: dict[str, HeroSpec] = {}
    for path in sorted(d.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to parse hero wiki %s", path, exc_info=True)
            continue
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("rarity"):
            continue
        hid = str(raw["id"])
        catalog[hid] = HeroSpec(
            id=hid,
            name=str(raw.get("name") or hid),
            rarity=str(raw.get("rarity")),
            hero_class=str(raw.get("class") or ""),
            sub_class=str(raw.get("sub_class") or "Combat"),
            shard_tiers=parse_shard_tiers(raw),
        )
    return catalog


def catalog_subclass_index(catalog: Mapping[str, HeroSpec]) -> dict[str, list[str]]:
    """Hero ids grouped by sub_class (Combat / Growth) — for quick lookups."""
    out: dict[str, list[str]] = {}
    for spec in catalog.values():
        out.setdefault(spec.sub_class, []).append(spec.id)
    return out
