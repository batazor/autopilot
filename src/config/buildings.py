"""Building registry loader.

Preferred source of truth:
  - `db/buildings/index.yaml` + `db/buildings/<id>.yaml`

Legacy (fallback) source:
  - `db/buildings.yaml`
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import yaml

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class BuildingRequire:
    """Explicit dependency edge: this building needs ``building`` at ``level``."""

    building: str
    level: int


@dataclass(frozen=True)
class BuildingDef:
    id: str
    name: str
    category: str = "unknown"
    requires: tuple[BuildingRequire, ...] = ()
    requirements_by_level: dict[int, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildingRegistry:
    buildings: tuple[BuildingDef, ...]

    def by_id(self, building_id: str) -> BuildingDef | None:
        building_id = (building_id or "").strip()
        if not building_id:
            return None
        for b in self.buildings:
            if b.id == building_id:
                return b
        return None

    def all_ids(self) -> list[str]:
        return [b.id for b in self.buildings]


def buildings_db_dir(repo_root: Path | None = None) -> Path:
    """Canonical location for the per-game buildings reference DB.

    Post-Phase-3 layout: ``games/<game>/db/buildings/`` (Phase-3 moved
    per-game reference data out of the root ``db/`` tree). Sync scripts and
    the wiki loader resolve through this helper to stay consistent.
    """
    from config.games import default_game, modules_root_for

    return modules_root_for(default_game(), repo_root=repo_root) / "db" / "buildings"


def load_buildings(path: Path | None = None) -> BuildingRegistry:
    # New format: games/<game>/db/buildings/index.yaml + per-building files
    buildings_dir = buildings_db_dir()
    index_path = buildings_dir / "index.yaml"
    if path is None and index_path.exists():
        idx = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        idx_buildings = idx.get("buildings", []) if isinstance(idx, dict) else []
        raw_items: list[dict[str, object]] = []
        if isinstance(idx_buildings, list):
            for it in idx_buildings:
                if not isinstance(it, dict):
                    continue
                bid = str(it.get("id") or "").strip()
                file_rel = str(it.get("file") or "").strip() or f"{bid}.yaml"
                if not bid:
                    continue
                p = buildings_dir / file_rel
                if not p.exists():
                    continue
                item_raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(item_raw, dict):
                    raw_items.append(item_raw)
        raw = {"buildings": raw_items}
    else:
        # Legacy single-file format (used only when path is explicitly supplied)
        if path is None:
            from config.paths import repo_root

            path = repo_root() / "db" / "buildings.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    buildings_raw = raw.get("buildings", [])
    buildings: list[BuildingDef] = []
    if isinstance(buildings_raw, list):
        for item in buildings_raw:
            if not isinstance(item, dict):
                continue

            rid = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not rid or not name:
                continue

            category = str(item.get("category") or "unknown").strip() or "unknown"

            requires_raw = item.get("requires") or []
            requires: list[BuildingRequire] = []
            if isinstance(requires_raw, list):
                for r in requires_raw:
                    if not isinstance(r, dict):
                        continue
                    dep = str(r.get("building") or "").strip()
                    if not dep:
                        continue
                    try:
                        lvl = int(r.get("level") or 1)
                    except (TypeError, ValueError):
                        lvl = 1
                    requires.append(BuildingRequire(building=dep, level=lvl))

            req_raw = item.get("requirements_by_level") or {}
            req_by_level: dict[int, dict[str, object]] = {}
            if isinstance(req_raw, dict):
                for level_k, level_v in req_raw.items():
                    try:
                        if isinstance(level_k, (int, float, str, bytes, bytearray)):
                            level = int(level_k)
                        else:
                            continue
                    except (TypeError, ValueError):
                        continue
                    if isinstance(level_v, dict):
                        req_by_level[level] = cast("dict[str, object]", level_v)

            buildings.append(
                BuildingDef(
                    id=rid,
                    name=name,
                    category=category,
                    requires=tuple(requires),
                    requirements_by_level=req_by_level,
                )
            )

    return BuildingRegistry(buildings=tuple(buildings))


# ---------------------------------------------------------------------------
# Global registry cache (mirrors `config.devices` style)
# ---------------------------------------------------------------------------

_registry: BuildingRegistry | None = None
_registry_lock = threading.Lock()


def invalidate_building_registry() -> None:
    global _registry
    with _registry_lock:
        _registry = None


def get_building_registry() -> BuildingRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_buildings()
    return _registry

