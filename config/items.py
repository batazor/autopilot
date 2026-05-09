"""Item registry loader.

Source of truth:
  - `db/items/index.yaml` + `db/items/<id>.yaml`
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    wiki_url: str = ""
    category: str = ""
    description: str = ""
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemRegistry:
    items: tuple[ItemDef, ...]

    def by_id(self, item_id: str) -> ItemDef | None:
        item_id = (item_id or "").strip()
        if not item_id:
            return None
        for it in self.items:
            if it.id == item_id:
                return it
        return None


def load_items() -> ItemRegistry:
    repo = Path(__file__).parent.parent
    items_dir = repo / "db" / "items"
    index_path = items_dir / "index.yaml"
    if not index_path.exists():
        return ItemRegistry(items=())

    idx = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    idx_items = idx.get("items", []) if isinstance(idx, dict) else []

    items: list[ItemDef] = []
    if isinstance(idx_items, list):
        for row in idx_items:
            if not isinstance(row, dict):
                continue
            iid = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            file_rel = str(row.get("file") or "").strip() or f"{iid}.yaml"
            if not iid or not name:
                continue
            p = items_dir / file_rel
            if not p.exists():
                continue
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue

            src_raw = raw.get("sources", [])
            sources: tuple[str, ...] = ()
            if isinstance(src_raw, list):
                sources = tuple(str(s) for s in src_raw if isinstance(s, str))

            items.append(
                ItemDef(
                    id=iid,
                    name=name,
                    wiki_url=str(raw.get("wiki_url") or ""),
                    category=str(raw.get("category") or ""),
                    description=str(raw.get("description") or ""),
                    sources=sources,
                )
            )

    return ItemRegistry(items=tuple(items))


_registry: ItemRegistry | None = None
_registry_lock = threading.Lock()


def invalidate_item_registry() -> None:
    global _registry  # noqa: PLW0603
    with _registry_lock:
        _registry = None


def get_item_registry() -> ItemRegistry:
    global _registry  # noqa: PLW0603
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_items()
    return _registry

