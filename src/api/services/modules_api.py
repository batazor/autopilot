"""Module catalog API (no Streamlit)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from config.devices import player_ids_for_device_candidates
from config.loader import load_settings
from config.module_discovery import (
    _clear_module_discovery_caches,
    is_core_nested_module,
    iter_module_dirs,
    load_module_yaml,
    module_matches_scope,
    module_meta_id,
    module_storage_key,
)
from config.module_registry import normalize_module_scope, path_matches_module_scope
from config.paths import repo_root
from dashboard.redis_client import get_player_scenario, set_player_scenario
from dsl import template_resolver as _tmpl
from dsl.registry import scenario_source_label

_REPO = repo_root()

_MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALLOWED_MODULE_PARENTS: frozenset[str] = frozenset(
    {"", "account", "core", "deals", "alliance", "events"},
)


def _context_cache_key(context: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(context.items()))


class _ScenarioListCache:
    """Per-request cache: 430 resolved keys share ~50 YAML paths.

    Listing only needs rendered ``name``; ``enabled``, ``device_level``, and
    ``steps`` length are identical for every expansion of a template file.
    """

    __slots__ = ("_base", "_names")

    def __init__(self) -> None:
        self._base: dict[Path, dict[str, Any]] = {}
        self._names: dict[tuple[Path, tuple[tuple[str, str], ...]], str] = {}

    def _base_doc(self, path: Path) -> dict[str, Any]:
        cached = self._base.get(path)
        if cached is not None:
            return cached
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw: dict[str, Any] = {}
        else:
            raw = loaded if isinstance(loaded, dict) else {}
        self._base[path] = raw
        return raw

    def display_name(self, path: Path, *, context: dict[str, str], key: str) -> str:
        if not context:
            return str(self._base_doc(path).get("name") or key)
        ck = (path, _context_cache_key(context))
        cached = self._names.get(ck)
        if cached is not None:
            return cached
        name_raw = self._base_doc(path).get("name")
        if not isinstance(name_raw, str) or "${" not in name_raw:
            name = str(name_raw or key)
        else:
            try:
                name = _tmpl.render(name_raw, context).strip() or key
            except Exception:
                name = key
        self._names[ck] = name
        return name


def _load_scenario_raw(path: Path, *, context: dict[str, str]) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        rendered = _tmpl.render(text, context)
    except Exception:
        return {}
    try:
        raw = yaml.safe_load(rendered) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _scenario_row(
    path: Path,
    *,
    context: dict[str, str],
    key: str,
    cache: _ScenarioListCache | None = None,
) -> dict[str, Any]:
    if cache is not None:
        raw = cache._base_doc(path)
        name = cache.display_name(path, context=context, key=key)
    else:
        raw = _load_scenario_raw(path, context=context)
        name = str(raw.get("name") or key)
    steps = raw.get("steps")
    enabled = raw.get("enabled")
    return {
        "key": key,
        "name": name,
        "enabled": enabled if isinstance(enabled, bool) else None,
        "device_level": raw.get("device_level") is True,
        "steps": len(steps) if isinstance(steps, list) else 0,
        "source": scenario_source_label(path, _REPO),
        "path": path.relative_to(_REPO).as_posix(),
    }


def list_scenarios(
    *,
    module_scope: str = "all",
    game: str | None = None,
) -> list[dict[str, Any]]:
    cache = _ScenarioListCache()
    out: list[dict[str, Any]] = []
    for rk in _tmpl.iter_resolved_keys(_REPO):
        if not path_matches_module_scope(rk.path, _REPO, module_scope, game=game):
            continue
        out.append(
            _scenario_row(rk.path, context=rk.context, key=rk.key, cache=cache),
        )
    out.sort(key=lambda r: (r["source"], r["key"]))
    return out


def _group_scenarios_by_module(
    module_dirs: list[Path],
) -> dict[Path, list[dict[str, Any]]]:
    """One pass over resolved scenario keys, bucketed by owning module root."""
    resolved_dirs = [d.resolve() for d in module_dirs]
    by_module: dict[Path, list[dict[str, Any]]] = {d: [] for d in resolved_dirs}
    depth_sorted = sorted(resolved_dirs, key=lambda p: len(p.parts), reverse=True)
    cache = _ScenarioListCache()
    for rk in _tmpl.iter_resolved_keys(_REPO):
        path_resolved = rk.path.resolve()
        owner: Path | None = None
        for module_dir in depth_sorted:
            if module_dir in path_resolved.parents:
                owner = module_dir
                break
        if owner is None:
            continue
        by_module[owner].append(
            _scenario_row(rk.path, context=rk.context, key=rk.key, cache=cache),
        )
    for rows in by_module.values():
        rows.sort(key=lambda r: r["key"])
    return by_module


def list_modules(
    *,
    module_scope: str = "all",
    game: str | None = None,
) -> list[dict[str, Any]]:
    scope = normalize_module_scope(module_scope)
    module_dirs = [
        module_dir
        for module_dir in iter_module_dirs(_REPO, game=game)
        if module_matches_scope(module_dir, scope, _REPO, game=game)
    ]
    scenarios_by_module = _group_scenarios_by_module(module_dirs)
    out: list[dict[str, Any]] = []
    for module_dir in module_dirs:
        meta = load_module_yaml(module_dir)
        module_id = module_meta_id(module_dir)
        title = str(meta.get("title") or module_id).strip() or module_id
        description = str(meta.get("description") or "").strip()
        wiki_raw = meta.get("wiki")
        wiki = wiki_raw is not False
        scen_decl = str(meta.get("scenarios") or "scenarios").strip()
        scen_dir = (module_dir / scen_decl).resolve() if scen_decl else None
        has_scenarios = scen_dir is not None and scen_dir.is_dir()
        analyze_path = module_dir / "analyze" / "analyze.yaml"
        if not analyze_path.is_file():
            analyze_decl = str(meta.get("analyze") or "").strip()
            if analyze_decl:
                analyze_path = (module_dir / analyze_decl).resolve()
        scenarios = scenarios_by_module.get(module_dir.resolve(), []) if has_scenarios else []
        enabled_on = sum(1 for s in scenarios if s.get("enabled") is True)
        enabled_off = sum(1 for s in scenarios if s.get("enabled") is False)
        out.append(
            {
                "id": module_id,
                "storage_key": module_storage_key(module_dir, _REPO, game=game),
                "title": title,
                "description": description,
                "wiki": wiki,
                "core": is_core_nested_module(module_dir, _REPO, game=game),
                "rel_path": module_dir.relative_to(_REPO).as_posix(),
                "scenarios_dir": scen_dir.relative_to(_REPO).as_posix()
                if has_scenarios and scen_dir is not None
                else None,
                "has_analyze": analyze_path.is_file(),
                "scenario_count": len(scenarios),
                "enabled_on": enabled_on,
                "enabled_off": enabled_off,
                "scenarios": scenarios,
            }
        )
    return out


def set_scenario_enabled(key: str, enabled: bool) -> None:
    resolved = _tmpl.resolve(_REPO, key)
    if resolved is None:
        msg = f"unknown scenario: {key}"
        raise KeyError(msg)
    path = resolved.path
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = "invalid scenario yaml"
        raise TypeError(msg)
    raw["enabled"] = enabled
    path.write_text(
        yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )


def list_players_with_assignments() -> list[dict[str, Any]]:
    settings = load_settings()
    seen: set[str] = set()
    players: list[str] = []
    for inst in settings.instances:
        for pid in player_ids_for_device_candidates(
            inst.bluestacks_window_title,
            inst.instance_id,
        ):
            if pid and pid not in seen:
                seen.add(pid)
                players.append(pid)
    from api.deps import get_redis

    client = get_redis()
    return [
        {
            "player_id": pid,
            "assigned_scenario": get_player_scenario(client, pid),
        }
        for pid in sorted(players)
    ]


def set_player_assignment(player_id: str, scenario_id: str | None) -> None:
    from api.deps import get_redis

    set_player_scenario(get_redis(), player_id, scenario_id or None)


def create_module(
    *,
    module_id: str,
    title: str,
    description: str = "",
    parent: str = "",
    wiki: bool = False,
    game: str | None = None,
) -> dict[str, Any]:
    """Scaffold a new module under ``games/<game>/[<parent>/]<id>/``.

    Writes ``module.yaml``, an empty ``analyze/analyze.yaml``, and a
    ``scenarios/.gitkeep`` so the overlay engine and scenario loader pick the
    module up immediately. Caller is responsible for committing the new files.
    """
    mid = (module_id or "").strip()
    if not _MODULE_ID_RE.match(mid):
        msg = (
            "module id must start with a lowercase letter and contain only "
            "lowercase letters, digits, and underscores"
        )
        raise ValueError(msg)
    parent_norm = (parent or "").strip()
    if parent_norm not in ALLOWED_MODULE_PARENTS:
        allowed = ", ".join(sorted(p or "(root)" for p in ALLOWED_MODULE_PARENTS))
        msg = f"unsupported parent '{parent_norm}' (allowed: {allowed})"
        raise ValueError(msg)
    title_norm = (title or "").strip() or mid
    desc_norm = (description or "").strip()

    from config.games import default_game, modules_root_for

    g = (game or default_game()).strip()
    modules_root = modules_root_for(g, repo_root=_REPO)
    module_dir = modules_root / parent_norm / mid if parent_norm else modules_root / mid

    if module_dir.exists():
        rel = module_dir.relative_to(_REPO).as_posix()
        msg = f"module path already exists: {rel}"
        raise FileExistsError(msg)
    existing_ids = {module_meta_id(d) for d in iter_module_dirs(_REPO, game=g)}
    if mid in existing_ids:
        msg = f"module id already taken: {mid}"
        raise FileExistsError(msg)

    module_dir.mkdir(parents=True, exist_ok=False)
    (module_dir / "analyze").mkdir()
    (module_dir / "scenarios").mkdir()

    manifest: dict[str, Any] = {
        "id": mid,
        "title": title_norm,
        "description": desc_norm,
        "scenarios": "scenarios",
        "analyze": "analyze/analyze.yaml",
        "wiki": bool(wiki),
    }
    (module_dir / "module.yaml").write_text(
        yaml.dump(
            manifest,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (module_dir / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (module_dir / "scenarios" / ".gitkeep").write_text("", encoding="utf-8")

    _clear_module_discovery_caches()

    rel_target = module_dir.relative_to(_REPO).as_posix()
    for row in list_modules(module_scope="all"):
        if row["rel_path"] == rel_target:
            return row
    msg = "module created but not visible to list_modules"  # pragma: no cover
    raise RuntimeError(msg)
