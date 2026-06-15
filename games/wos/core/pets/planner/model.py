"""Static pet catalog parsed from ``games/wos/db/pets/*.yaml``.

Pure parsing + lookups. Feeds :mod:`planner`, which decides which pet to invest
the next pet-food/shards into.

Each pet yaml has ``rarity`` (SSR vs ordinary), an ``unlock`` gate (free text like
"Unlock after 200 days and Snow Leopard LV.30" — a server-age threshold + a
prerequisite pet level, the pet analogue of a generation gate, but read straight
from data), a ``skill`` (effect scaling per level) and a ``troop_bonus``. The skill
is classified into a category (march / gather / construction / stamina / combat)
so role weighting knows whether a pet helps the economy, combat, or is universal.
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

# games/wos/core/pets/planner/ → parents[3] = games/wos
DEFAULT_PETS_DIR = Path(__file__).resolve().parents[3] / "db" / "pets"

_DAYS_RE = re.compile(r"(\d+)\s*[Dd]ay")
_PREREQ_RE = re.compile(r"and\s+([A-Za-z][A-Za-z '\-]+?)\s+LV\.?\s*(\d+)", re.IGNORECASE)

_CATEGORY_KEYWORDS = (
    ("march", ("march",)),
    ("gather", ("gather", "unearth", "finding", "locate", "load", "burden", "carry",
                "intuition")),
    ("construction", ("construction", "architect", "builder", "tools")),
    ("stamina", ("stamina", "rejuven", "weary", "comfort", "embrace")),
)


def _norm(name: str) -> str:
    return re.sub(r"[ '\-]+", "_", name.strip().lower())


def parse_unlock(text: Any) -> tuple[int | None, tuple[str, int] | None]:
    """``"after 200 days and Snow Leopard LV.30"`` → (200, ("snow leopard", 30))."""
    s = str(text or "")
    days_m = _DAYS_RE.search(s)
    days = int(days_m.group(1)) if days_m else None
    pre_m = _PREREQ_RE.search(s)
    prereq = (pre_m.group(1).strip(), int(pre_m.group(2))) if pre_m else None
    return days, prereq


def categorize_skill(skill: Any, troop_bonus: Any) -> str:
    """Classify a pet by what its skill / bonus does (default → combat)."""
    parts: list[str] = []
    if isinstance(skill, dict):
        parts += [str(skill.get("name", "")), str(skill.get("effect", ""))]
    if isinstance(troop_bonus, dict):
        parts.append(str(troop_bonus.get("stat", "")))
    hay = " ".join(parts).lower()
    for category, words in _CATEGORY_KEYWORDS:
        if any(w in hay for w in words):
            return category
    return "combat"


@dataclass(frozen=True, slots=True)
class PetSpec:
    """One pet's static profile relevant to investment decisions."""

    id: str
    name: str
    rarity: str                          # "SSR" or "" (ordinary)
    unlock_days: int | None              # server age gate
    prereq: tuple[str, int] | None       # (prereq pet id, level) gate
    skill_name: str
    category: str                        # march | gather | construction | stamina | combat


def load_pet_catalog(directory: str | Path | None = None) -> dict[str, PetSpec]:
    """Parse every pet yaml into ``id → PetSpec`` (resolving prerequisite names)."""
    d = Path(directory) if directory else DEFAULT_PETS_DIR
    raws: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to parse pet yaml %s", path, exc_info=True)
            continue
        if isinstance(raw, dict) and raw.get("id"):
            raws.append(raw)

    name_to_id = {_norm(str(r.get("name") or r["id"])): str(r["id"]) for r in raws}
    catalog: dict[str, PetSpec] = {}
    for raw in raws:
        days, prereq_raw = parse_unlock(raw.get("unlock"))
        prereq = None
        if prereq_raw is not None:
            pre_id = name_to_id.get(_norm(prereq_raw[0]))
            if pre_id:
                prereq = (pre_id, prereq_raw[1])
        skill = raw.get("skill")
        catalog[str(raw["id"])] = PetSpec(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            rarity=str(raw.get("rarity") or ""),
            unlock_days=days,
            prereq=prereq,
            skill_name=str(skill.get("name", "")) if isinstance(skill, dict) else "",
            category=categorize_skill(skill, raw.get("troop_bonus")),
        )
    return catalog


def catalog_category_index(catalog: Mapping[str, PetSpec]) -> dict[str, list[str]]:
    """Pet ids grouped by skill category."""
    out: dict[str, list[str]] = {}
    for spec in catalog.values():
        out.setdefault(spec.category, []).append(spec.id)
    return out
