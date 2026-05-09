"""Hero registry loader.

Preferred source of truth:
  - `db/heroes/index.yaml` + `db/heroes/<id>.yaml`
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class HeroDef:
    id: str
    name: str
    wiki_url: str = ""
    rarity: str = ""
    hero_class: str = ""
    sub_class: str = ""
    skills: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class HeroRegistry:
    heroes: tuple[HeroDef, ...]

    def by_id(self, hero_id: str) -> HeroDef | None:
        hero_id = (hero_id or "").strip()
        if not hero_id:
            return None
        for h in self.heroes:
            if h.id == hero_id:
                return h
        return None


def load_heroes() -> HeroRegistry:
    repo = Path(__file__).parent.parent
    heroes_dir = repo / "db" / "heroes"
    index_path = heroes_dir / "index.yaml"
    if not index_path.exists():
        return HeroRegistry(heroes=())

    idx = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    idx_heroes = idx.get("heroes", []) if isinstance(idx, dict) else []

    heroes: list[HeroDef] = []
    if isinstance(idx_heroes, list):
        for it in idx_heroes:
            if not isinstance(it, dict):
                continue
            hid = str(it.get("id") or "").strip()
            name = str(it.get("name") or "").strip()
            file_rel = str(it.get("file") or "").strip() or f"{hid}.yaml"
            if not hid or not name:
                continue
            p = heroes_dir / file_rel
            if not p.exists():
                continue
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue
            skills_raw = raw.get("skills", [])
            skills: tuple[dict[str, str], ...] = ()
            if isinstance(skills_raw, list):
                skills = tuple(
                    {
                        "name": str(s.get("name") or ""),
                        "description": str(s.get("description") or ""),
                    }
                    for s in skills_raw
                    if isinstance(s, dict)
                )

            heroes.append(
                HeroDef(
                    id=hid,
                    name=name,
                    wiki_url=str(raw.get("wiki_url") or ""),
                    rarity=str(raw.get("rarity") or ""),
                    hero_class=str(raw.get("class") or ""),
                    sub_class=str(raw.get("sub_class") or ""),
                    skills=skills,
                )
            )

    return HeroRegistry(heroes=tuple(heroes))


_registry: HeroRegistry | None = None
_registry_lock = threading.Lock()


def invalidate_hero_registry() -> None:
    global _registry  # noqa: PLW0603
    with _registry_lock:
        _registry = None


def get_hero_registry() -> HeroRegistry:
    global _registry  # noqa: PLW0603
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_heroes()
    return _registry

