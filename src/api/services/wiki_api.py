"""Wiki reference API (db/ + modules/*/wiki/)."""
from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal

import yaml

from config.module_registry import ALL_MODULES_KEY, module_scope_options
from config.paths import repo_root
from config.wiki_sources import EntityKey, WikiEntry, find_entry, load_merged_entries
from wiki.sync_runner import SYNC_SCRIPT_SPECS, aiter_sync_ndjson, get_sync_spec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_REPO = repo_root()
EntitySection = Literal["buildings", "heroes", "items", "gear", "faq"]


def _request_game() -> str:
    from api.services.game_resolver import current_request_game

    return current_request_game()


def list_scopes() -> list[dict[str, str]]:
    return [
        {"key": k, "label": lab}
        for k, lab in module_scope_options(_REPO, game=_request_game())
    ]


def _entry_summary(e: WikiEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "source": e.source,
        "wiki_url": str(e.entry.get("wiki_url") or "").strip(),
        "has_icon": e.icon_path is not None and e.icon_path.is_file(),
        "yaml_path": str(e.yaml_path.relative_to(_REPO)) if e.yaml_path else "",
    }


def list_entity_entries(
    entity: EntityKey,
    *,
    scope: str = ALL_MODULES_KEY,
    query: str = "",
) -> dict[str, Any]:
    entries = load_merged_entries(
        entity, repo_root=_REPO, module_scope=scope, game=_request_game()
    )
    q = query.strip().lower()
    rows = []
    for e in entries:
        hay = f"{e.name} {e.id}".lower()
        if q and q not in hay:
            continue
        rows.append(_entry_summary(e))
    return {"entity": entity, "scope": scope, "entries": rows, "count": len(rows)}


def get_entity_detail(entity: EntityKey, entity_id: str, *, scope: str = ALL_MODULES_KEY) -> dict[str, Any]:
    target = entity_id.strip()
    for e in load_merged_entries(
        entity, repo_root=_REPO, module_scope=scope, game=_request_game()
    ):
        if e.id == target:
            body = _load_yaml_dict(e.yaml_path) if e.yaml_path.is_file() else {}
            return {
                "entity": entity,
                "summary": _entry_summary(e),
                "body": body,
            }
    msg = f"{entity} entry not found: {entity_id}"
    raise KeyError(msg)


def read_icon(entity: EntityKey, entity_id: str) -> tuple[bytes, str]:
    entry = find_entry(entity, entity_id, repo_root=_REPO, game=_request_game())
    if entry is None or entry.icon_path is None or not entry.icon_path.is_file():
        msg = "icon not found"
        raise FileNotFoundError(msg)
    path = entry.icon_path
    ext = path.suffix.lower()
    mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
    return path.read_bytes(), mime


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def list_gear() -> dict[str, Any]:
    gear_dir = _REPO / "db" / "gear"
    if not gear_dir.is_dir():
        return {"entries": [], "missing_dir": True}
    entries: list[dict[str, str]] = []
    for p in sorted(gear_dir.glob("*.yaml")):
        if not p.is_file():
            continue
        doc = _load_yaml_dict(p)
        title = str(doc.get("title") or doc.get("id") or p.stem)
        entries.append({"id": p.stem, "title": title, "file": p.name})
    return {"entries": entries, "missing_dir": False}


def get_gear_detail(gear_id: str) -> dict[str, Any]:
    gear_dir = _REPO / "db" / "gear"
    path = gear_dir / f"{gear_id}.yaml"
    if gear_id == "enhancement":
        path = gear_dir / "enhancement.yaml"
    if not path.is_file():
        msg = f"gear not found: {gear_id}"
        raise FileNotFoundError(msg)
    return {"id": gear_id, "file": path.name, "body": _load_yaml_dict(path)}


def _faq_sync_items() -> list[dict[str, Any]]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "script": spec.script_rel,
            "args": list(spec.args) or None,
        }
        for spec in SYNC_SCRIPT_SPECS.values()
    ]


FAQ_BODY = {
    "title": "FAQ",
    "sections": [
        {
            "heading": "Where does the data come from?",
            "text": (
                "Wiki data is scraped into db/ YAML files from whiteoutsurvival.wiki. "
                "Modules can contribute under modules/<id>/wiki/."
            ),
        },
        {
            "heading": "Sync scripts",
            "text": (
                "Use the buttons below to run sync scripts with live progress, "
                "or run from the repo root: `uv run python <script>`."
            ),
            "items": _faq_sync_items(),
        },
    ],
}


def get_faq() -> dict[str, Any]:
    return FAQ_BODY


async def stream_sync_script(script_key: str) -> AsyncIterator[bytes]:
    spec = get_sync_spec(script_key)
    async for chunk in aiter_sync_ndjson(spec, repo=_REPO):
        yield chunk
