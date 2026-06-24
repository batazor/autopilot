"""Static building dependency graph parsed from ``games/wos/db/buildings/*.yaml``.

No Redis, no ADB, no game IO — pure parsing + lookups, unit testable. The graph
feeds :mod:`planner`, which decides *which building to upgrade next* (the bot has
no such logic today — it just upgrades whatever is under the camera).

Each ``db/buildings/<id>.yaml`` carries, per level, the in-game ``prerequisites``
("Embassy Lv. 10 Lancer Camp Lv. 10"), the ``build_cost`` (resource items), the
``construction_time``, and ``building_power``. The prerequisite text is the
authoritative per-level gate — it encodes both the explicit deps (Furnace 11
needs Embassy 10) and the universal "a building can't exceed the Furnace level"
cap (Embassy 9 needs Furnace Lv. 9). We parse it into structured
``(building_id, rank)`` pairs against a name→id index built from every spec.

Levels are modelled by *rank* (a float), not the raw key, so numeric levels and
post-30 Fire-Crystal levels ("FC-8", "30-8") order correctly: ``FC-n`` → ``30+n``.
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

# games/wos/core/building/planner/ → parents[3] = games/wos
DEFAULT_BUILDINGS_DIR = Path(__file__).resolve().parents[3] / "db" / "buildings"

_AMOUNT_RE = re.compile(r"([\d.]+)\s*([kKmMbB]?)")
_AMOUNT_MULT = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

# Build costs in the YAML use opaque sprite ids (``item_icon_103`` …). This map
# decodes them to canonical resource names so costs/shortfalls are per-resource
# (meat/wood/coal/iron …) — which feeds the resource allocator and lets bottleneck
# repair target the right producer. Derived by cross-referencing the Fire-Crystal
# specs against db/fire_crystal_costs.yaml's named amounts (NOT scraped): 104/105/
# 100081 are pinned by their amounts (coal 13M, iron 3.3M, fire_crystal 132 at
# Furnace FC1); 100082 is the only other special icon (refined fire crystal, enters
# at FC5). 100011 and 103 are the two basic resources that are always cost-equal in
# the data (meat == wood at every level), so the meat/wood split follows the
# canonical in-game order and is interchangeable for affordability. Unknown ids pass
# through unchanged (the cost is still summed; it just isn't resource-attributed).
ITEM_RESOURCE: dict[str, str] = {
    "item_icon_102": "meat",
    "item_icon_100011": "meat",
    "item_icon_103": "wood",
    "item_icon_104": "coal",
    "item_icon_105": "iron",
    "item_icon_100081": "fire_crystal",
    "item_icon_100082": "refined_fire_crystal",
}


def resource_name(item_id: Any) -> str:
    """Canonical resource name for a build-cost item id (passthrough if unmapped)."""
    s = str(item_id)
    return ITEM_RESOURCE.get(s, s)
_DAYS_RE = re.compile(r"(\d+)\s*d")
_HMS_RE = re.compile(r"(\d+):(\d+):(\d+)")
# "Lv. 10" / "Lvl. 8" / "Lvl 8" / "FC-8" — the level token trailing a building name.
_LEVEL_TOKEN_RE = re.compile(r"\s*(?:Lvl?\.?\s*(\d+)|FC-?(\d+)|30-(\d+))", re.IGNORECASE)


def parse_amount(value: Any) -> int:
    """``"67M"`` → 67_000_000, ``"3.3M"`` → 3_300_000, ``"460k"`` → 460_000."""
    if value is None:
        return 0
    s = str(value).strip().replace(",", "")
    m = _AMOUNT_RE.match(s)
    if not m:
        return 0
    return int(float(m.group(1)) * _AMOUNT_MULT[m.group(2).lower()])


def parse_duration(value: Any) -> int:
    """Seconds from ``"7d"``, ``"04:30:00"``, ``"33d 11:42:00"``, ``"00:00:02"``."""
    if value is None:
        return 0
    s = str(value).strip()
    total = 0
    d = _DAYS_RE.search(s)
    if d:
        total += int(d.group(1)) * 86_400
    hms = _HMS_RE.search(s)
    if hms:
        h, m, sec = (int(x) for x in hms.groups())
        total += h * 3_600 + m * 60 + sec
    return total


def level_rank(value: Any) -> float:
    """Comparable rank for a level: plain ints as-is; Fire-Crystal levels after 30
    (``FC-8`` / ``30-8`` → 38). ``0`` for unbuilt / unparseable."""
    if value is None or value == "":
        return 0.0
    s = str(value).strip()
    if s.isdigit():
        return float(s)
    m = re.match(r"(?:FC-?|30-)(\d+)", s, re.IGNORECASE)
    if m:
        return 30.0 + int(m.group(1))
    m = re.search(r"\d+", s)
    return float(m.group()) if m else 0.0


def parse_prerequisites(
    text: Any, name_to_id: Mapping[str, str]
) -> tuple[tuple[str, float], ...]:
    """Parse ``"Embassy Lv. 10 Lancer Camp Lv. 9"`` → ``((embassy,10),(lancer_camp,9))``.

    Scans for known building *names* (longest first, so "Research Center" wins
    over a bare "Research"), then reads the optional trailing level token. A name
    with no level (e.g. "… Research Center") defaults to rank 1 (must exist).
    Unknown names are skipped — a parse miss must not invent a dependency.
    """
    if not text:
        return ()
    s = str(text).replace("’", "'").strip()
    names = sorted(name_to_id, key=len, reverse=True)
    if not names:
        return ()
    name_re = re.compile("|".join(re.escape(n) for n in names))
    out: list[tuple[str, float]] = []
    for m in name_re.finditer(s):
        bid = name_to_id[m.group(0)]
        lvl = _LEVEL_TOKEN_RE.match(s, m.end())
        if lvl:
            num = lvl.group(1)
            rank = float(num) if num else 30.0 + int(lvl.group(2) or lvl.group(3))
        else:
            rank = 1.0
        out.append((bid, rank))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class LevelReq:
    """One level of a building: its gate, cost, time and power."""

    level: str                                   # raw key ("10", "30-1")
    rank: float
    prereqs: tuple[tuple[str, float], ...]       # (building_id, required rank)
    cost: tuple[tuple[str, int], ...]            # (resource name, amount) — see ITEM_RESOURCE
    time_s: int
    power: int | None


@dataclass(frozen=True, slots=True)
class BuildingSpec:
    """One building's full level ladder, ordered by rank."""

    id: str
    name: str
    levels: tuple[LevelReq, ...]

    @property
    def max_rank(self) -> float:
        return self.levels[-1].rank if self.levels else 0.0

    def next_after(self, current_rank: float) -> LevelReq | None:
        """The next level above ``current_rank`` (the one we'd build now)."""
        for lvl in self.levels:
            if lvl.rank > current_rank:
                return lvl
        return None

    def level(self, level_key: str) -> LevelReq | None:
        """The level entry for a raw key (``"10"`` / ``"30-1"``)."""
        return next((lvl for lvl in self.levels if lvl.level == level_key), None)


@dataclass(frozen=True, slots=True)
class BuildGraph:
    """Parsed building tree + a name→id index for resolving prerequisite text."""

    buildings: Mapping[str, BuildingSpec]

    def spec(self, building_id: str) -> BuildingSpec | None:
        return self.buildings.get(building_id)


def _decode_cost(build_cost: Any) -> tuple[tuple[str, int], ...]:
    """Build-cost entries → ``(resource_name, amount)``, summing same-resource icons.

    Decodes item-icon ids to canonical resource names (see :data:`ITEM_RESOURCE`) and
    merges duplicates (e.g. two meat icons) while preserving first-seen order.
    """
    out: dict[str, int] = {}
    for c in (build_cost or []):
        res = resource_name(c.get("item"))
        out[res] = out.get(res, 0) + parse_amount(c.get("amount"))
    return tuple(out.items())


def _build_spec(raw: dict[str, Any], name_to_id: Mapping[str, str]) -> BuildingSpec:
    levels: list[LevelReq] = []
    for key, entry in (raw.get("requirements_by_level") or {}).items():
        entry = entry or {}
        power = entry.get("building_power")
        levels.append(LevelReq(
            level=str(key),
            rank=level_rank(key),
            prereqs=parse_prerequisites(entry.get("prerequisites"), name_to_id),
            cost=_decode_cost(entry.get("build_cost")),
            time_s=parse_duration(entry.get("construction_time")),
            power=int(power) if isinstance(power, (int, float)) else None,
        ))
    levels.sort(key=lambda r: r.rank)
    return BuildingSpec(id=str(raw["id"]), name=str(raw.get("name") or raw["id"]),
                        levels=tuple(levels))


def load_graph(directory: str | Path | None = None) -> BuildGraph:
    """Parse every ``<id>.yaml`` under ``directory`` into a :class:`BuildGraph`.

    Two passes: collect ids+names to build the name→id index, then parse each
    spec's per-level prerequisite text against it.
    """
    d = Path(directory) if directory else DEFAULT_BUILDINGS_DIR
    raws: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to parse building spec %s", path, exc_info=True)
            continue
        if isinstance(raw, dict) and raw.get("id"):
            raws.append(raw)

    name_to_id = {str(r.get("name") or r["id"]).replace("’", "'"): str(r["id"]) for r in raws}
    buildings = {str(r["id"]): _build_spec(r, name_to_id) for r in raws}
    return BuildGraph(buildings=buildings)
