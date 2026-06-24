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
import re
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


# --- Hero expedition skill economy buffs -------------------------------------
# A handful of heroes carry city-throughput buffs in their skill text (Zinman →
# Building Upgrade speed, Jasser → Research Speed, Ling Xue → Training Speed, the
# gathering specialists → Gathering Speed, Seo-Yoon → infirmary Healing speed).
# These accelerate the very work the economy planners schedule, so the hero planner
# values them when deciding where skill books go. We tag the buff CATEGORY by the
# real buff phrase (not a generic word — "construction workflow" in Zinman's flavour
# text must not match) and read the per-skill-level percentage list nearest that
# phrase (the % sits before the phrase for gathering, after it for the rest).
_PCTS_RE = re.compile(r"\d+(?:\.\d+)?%(?:/\d+(?:\.\d+)?%)+")
_BUFF_CATEGORY: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("construction", re.compile(r"building upgrade speed|construction speed", re.IGNORECASE)),
    ("research", re.compile(r"research speed", re.IGNORECASE)),
    ("training", re.compile(r"training speed", re.IGNORECASE)),
    ("gather", re.compile(r"gathering speed", re.IGNORECASE)),
    ("heal", re.compile(r"healing speed", re.IGNORECASE)),
)


def _closest_pcts(desc: str, keyword: re.Pattern[str]) -> tuple[float, ...] | None:
    """The percentage list nearest the buff phrase (handles %-before and %-after)."""
    km = keyword.search(desc)
    if km is None:
        return None
    centre = (km.start() + km.end()) / 2
    best: re.Match[str] | None = None
    best_dist = None
    for m in _PCTS_RE.finditer(desc):
        dist = abs((m.start() + m.end()) / 2 - centre)
        if best_dist is None or dist < best_dist:
            best, best_dist = m, dist
    if best is None:
        return None
    return tuple(float(p.rstrip("%")) for p in best.group(0).split("/"))


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
class HeroSkill:
    """A hero skill that buffs city throughput, with its per-skill-level curve."""

    name: str
    category: str                    # construction | research | training | gather | heal
    levels: tuple[float, ...]        # buff % at skill level 1..N

    def buff_at(self, skill_level: int) -> float:
        """Active buff % at ``skill_level`` (0 = not unlocked; clamps to the curve)."""
        if skill_level <= 0 or not self.levels:
            return 0.0
        return self.levels[min(skill_level, len(self.levels)) - 1]

    @property
    def max_buff(self) -> float:
        return self.levels[-1] if self.levels else 0.0

    def marginal(self, from_level: int) -> float:
        """Buff % gained by raising the skill ``from_level`` → ``from_level + 1``."""
        return max(0.0, self.buff_at(from_level + 1) - self.buff_at(from_level))

    def remaining(self, from_level: int) -> float:
        """Buff % still unrealised above ``from_level`` (the full upgrade potential)."""
        return max(0.0, self.max_buff - self.buff_at(from_level))


def parse_economy_skills(raw_skills: Any) -> tuple[HeroSkill, ...]:
    """Extract the city-throughput skills (one per recognised buff category)."""
    out: list[HeroSkill] = []
    for sk in (raw_skills or []):
        if not isinstance(sk, dict):
            continue
        desc = " ".join(str(sk.get("description", "")).split())
        for cat, keyword in _BUFF_CATEGORY:
            levels = _closest_pcts(desc, keyword)
            if levels:
                out.append(HeroSkill(name=str(sk.get("name", "")), category=cat, levels=levels))
                break
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
    economy_skills: tuple[HeroSkill, ...] = ()   # city-throughput buffs (construction/…)

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
            economy_skills=parse_economy_skills(raw.get("skills")),
        )
    return catalog


def catalog_subclass_index(catalog: Mapping[str, HeroSpec]) -> dict[str, list[str]]:
    """Hero ids grouped by sub_class (Combat / Growth) — for quick lookups."""
    out: dict[str, list[str]] = {}
    for spec in catalog.values():
        out.setdefault(spec.sub_class, []).append(spec.id)
    return out
