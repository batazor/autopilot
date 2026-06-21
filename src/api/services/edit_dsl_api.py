"""Module DSL YAML editor API."""
from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from config.module_discovery import (
    iter_module_dirs,
    load_module_yaml,
    module_matches_scope,
    module_meta_id,
    module_storage_key,
)
from config.module_registry import normalize_module_scope
from config.paths import repo_root
from config.reference_naming import event_icon_abs_path
from config.startup_validation import duplicate_scenario_names_for_repo
from dashboard.area_doc import crop_path_for_entry_region
from dsl.cron_specs import _is_under_drafts
from dsl.dsl_schema import dump_scenario, parse_scenario
from dsl.registry import iter_scenario_yaml_files, scenario_roots, scenario_source_label
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from navigation.screen_graph import screen_verify_screen_names
from tasks.dsl_exec import DSL_EXEC_REGISTRY

_REPO = repo_root()


def _request_game() -> str:
    from api.services.game_resolver import current_request_game

    return current_request_game()


def _module_storage_for_path(path: Path) -> str | None:
    path_resolved = path.resolve()
    g = _request_game()
    for module_dir in iter_module_dirs(_REPO, game=g):
        module_resolved = module_dir.resolve()
        if module_resolved in path_resolved.parents:
            return module_storage_key(module_dir, _REPO, game=g)
    return None


def _is_readonly_scenario(path: Path) -> bool:
    try:
        rel_parts = path.relative_to(_REPO).parts
    except ValueError:
        rel_parts = path.parts
    return _is_under_drafts(rel_parts) or any(p == "by_cron" for p in rel_parts)


def list_editable_modules(*, module_scope: str = "all") -> list[dict[str, str]]:
    scope = normalize_module_scope(module_scope)
    g = _request_game()
    out: list[dict[str, str]] = []
    for module_dir in iter_module_dirs(_REPO, game=g):
        if not module_matches_scope(module_dir, scope, _REPO, game=g):
            continue
        meta = load_module_yaml(module_dir)
        scen_decl = str(meta.get("scenarios") or "scenarios").strip()
        scen_dir = module_dir / scen_decl
        if not scen_dir.is_dir():
            continue
        module_id = module_meta_id(module_dir)
        title = str(meta.get("title") or module_id).strip() or module_id
        out.append(
            {
                "key": module_storage_key(module_dir, _REPO, game=g),
                "title": title,
                "scenarios_dir": scen_dir.relative_to(_REPO).as_posix(),
            }
        )
    return out


def list_editable_files(*, module_scope: str = "all") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for _root, path in iter_scenario_yaml_files(_REPO, module_scope, game=_request_game()):
        if _is_readonly_scenario(path):
            continue
        rel = scenario_source_label(path, _REPO)
        module_key = _module_storage_for_path(path) or ""
        out.append(
            {
                "rel": rel,
                "stem": path.stem,
                "module": module_key,
                "path": path.relative_to(_REPO).as_posix(),
            }
        )
    out.sort(key=lambda r: r["rel"])
    return out


def build_tree(rel_paths: list[str]) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"files": [], "dirs": {}}
    for rel in rel_paths:
        parts = rel.split("/")
        node = root
        for part in parts[:-1]:
            node["dirs"].setdefault(part, {"files": [], "dirs": {}})
            node = node["dirs"][part]
        node["files"].append(rel)

    def _walk(node: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [
            {"value": rel, "title": Path(rel).stem, "is_dir": False}
            for rel in sorted(node["files"])
        ]
        for dirname in sorted(node["dirs"]):
            children = _walk(node["dirs"][dirname], f"{prefix}{dirname}/")
            if not children:
                continue
            out.append(
                {
                    "value": f"__dir__/{prefix}{dirname}",
                    "title": f"{dirname}/",
                    "is_dir": True,
                    "children": children,
                }
            )
        return out

    return _walk(root, "")


def list_catalog(*, module_scope: str = "all") -> dict[str, Any]:
    files = list_editable_files(module_scope=module_scope)
    rels = [f["rel"] for f in files]
    return {
        "scope": normalize_module_scope(module_scope),
        "files": files,
        "tree": build_tree(rels),
        "modules": list_editable_modules(module_scope=module_scope),
    }


def _path_for_rel(rel: str) -> Path:
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in Path(rel).parts:
        msg = "invalid path"
        raise ValueError(msg)
    path = (_REPO / rel).resolve()
    if not path.is_file() or path.suffix.lower() != ".yaml":
        msg = "scenario file not found"
        raise FileNotFoundError(msg)
    if _is_readonly_scenario(path):
        msg = "read-only path (drafts/ or by_cron/)"
        raise PermissionError(msg)
    return path


def _normalize_loaded_doc(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = "root must be a mapping"
        raise TypeError(msg)
    raw.setdefault("steps", [])
    if not isinstance(raw.get("steps"), list):
        raw["steps"] = []
    return raw


def get_file(rel: str) -> dict[str, Any]:
    path = _path_for_rel(rel)
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    try:
        doc = _normalize_loaded_doc(raw)
    except TypeError as exc:
        return {
            "rel": scenario_source_label(path, _REPO),
            "path": path.relative_to(_REPO).as_posix(),
            "stem": path.stem,
            "yaml": text,
            "document": {"name": path.stem, "steps": []},
            "name": path.stem,
            "valid": False,
            "validation_error": str(exc),
        }
    ok, err = _validate_doc(doc)
    return {
        "rel": scenario_source_label(path, _REPO),
        "path": path.relative_to(_REPO).as_posix(),
        "stem": path.stem,
        "yaml": text,
        "document": doc,
        "name": str(doc.get("name") or path.stem),
        "valid": ok,
        "validation_error": err,
    }


def _scenario_root_for_path(path: Path) -> Path:
    resolved = path.resolve()
    g = _request_game()
    for root in scenario_roots(_REPO, game=g):
        root_resolved = root.path.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return root.path
    return path.parent


def _resolve_module_scenarios_dir(module_key: str) -> Path:
    key = (module_key or "").strip()
    if not key:
        msg = "module required"
        raise ValueError(msg)
    g = _request_game()
    for module_dir in iter_module_dirs(_REPO, game=g):
        sk = module_storage_key(module_dir, _REPO, game=g)
        if sk != key and (":" not in sk or sk.split(":", 1)[1] != key):
            continue
        meta = load_module_yaml(module_dir)
        scen_decl = str(meta.get("scenarios") or "scenarios").strip()
        scen_dir = (module_dir / scen_decl).resolve()
        if scen_dir.is_dir():
            return scen_dir
        msg = f"module has no scenarios directory: {key}"
        raise ValueError(msg)
    msg = f"unknown module: {key}"
    raise ValueError(msg)


def _validate_doc(doc: dict[str, Any]) -> tuple[bool, str]:
    try:
        parse_scenario(doc)
    except ValidationError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
    return True, ""


def save_file(rel: str, *, yaml_text: str | None = None, document: dict[str, Any] | None = None) -> dict[str, Any]:
    path = _path_for_rel(rel)
    if document is not None:
        raw = _normalize_loaded_doc(document)
    elif yaml_text is not None:
        raw = yaml.safe_load(yaml_text) or {}
        if not isinstance(raw, dict):
            msg = "root must be a mapping"
            raise TypeError(msg)
        raw.setdefault("steps", [])
    else:
        msg = "yaml or document required"
        raise ValueError(msg)
    ok, err = _validate_doc(raw)
    if not ok:
        msg = err or "validation failed"
        raise ValueError(msg)
    parsed = parse_scenario(raw)
    out_doc = dump_scenario(parsed)

    scenario_root = _scenario_root_for_path(path)
    backups_root = scenario_root / ".backups"
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    backup_dir = backups_root / ts
    if path.is_file():
        rel_under = path.relative_to(scenario_root)
        backup_path = backup_dir / rel_under
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(out_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "rel": scenario_source_label(path, _REPO),
        "backup": str((backup_dir / path.relative_to(scenario_root)).as_posix())
        if path.is_file()
        else None,
    }


def validate_yaml(*, yaml_text: str | None = None, document: dict[str, Any] | None = None) -> dict[str, Any]:
    if document is not None:
        try:
            raw = _normalize_loaded_doc(document)
        except TypeError as exc:
            return {"valid": False, "error": str(exc)}
    elif yaml_text is not None:
        try:
            raw = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as exc:
            return {"valid": False, "error": str(exc), "preview": ""}
        if not isinstance(raw, dict):
            return {"valid": False, "error": "root must be a mapping", "preview": ""}
    else:
        return {"valid": False, "error": "yaml or document required", "preview": ""}
    ok, err = _validate_doc(raw)
    preview = ""
    if ok:
        try:
            preview = yaml.safe_dump(
                dump_scenario(parse_scenario(raw)),
                sort_keys=False,
                allow_unicode=True,
            )
        except Exception:
            preview = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    return {"valid": ok, "error": err, "preview": preview}


def create_file(
    *,
    module: str,
    file_key: str,
    template_rel: str = "",
) -> dict[str, Any]:
    key = re.sub(r"[^a-zA-Z0-9._-]+", "_", (file_key or "").strip()).strip("._-") or "scenario"
    if template_rel.strip():
        scenario_root = _scenario_root_for_path(_path_for_rel(template_rel))
    else:
        scenario_root = _resolve_module_scenarios_dir(module)
    new_path = scenario_root / f"{key}.yaml"
    if new_path.exists():
        msg = "file already exists"
        raise FileExistsError(msg)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    stub = {"name": key.replace("_", " "), "enabled": False, "steps": []}
    new_path.write_text(yaml.safe_dump(stub, sort_keys=False), encoding="utf-8")
    rel = scenario_source_label(new_path, _REPO)
    return {"ok": True, "rel": rel, "path": new_path.relative_to(_REPO).as_posix()}


def name_collisions(rel: str, name: str) -> list[str]:
    nm = (name or "").strip()
    if not nm:
        return []
    dups = duplicate_scenario_names_for_repo(_REPO)
    return [r for r in dups.get(nm, []) if r != rel.replace("\\", "/").strip().lstrip("/")]


def event_icon_path(slug: str) -> Path | None:
    return event_icon_abs_path(_REPO, slug)


def region_crop_path(region_name: str) -> Path | None:
    """Resolve the on-disk crop PNG for ``region_name`` (first matching screen)."""
    name = (region_name or "").strip()
    if not name:
        return None
    try:
        doc = load_area_doc(_REPO)
    except Exception:
        return None
    pair = screen_region_by_name(doc, name)
    if pair is None:
        return None
    entry, _ = pair
    crop = crop_path_for_entry_region(_REPO, entry, name)
    if crop is None or not crop.is_file():
        return None
    return crop


def editor_meta() -> dict[str, Any]:
    regions: list[str] = []
    region_refs: dict[str, str] = {}
    region_screens: dict[str, str] = {}
    region_red_dot: set[str] = set()
    try:
        doc = load_area_doc(_REPO)
    except Exception:
        doc = {}
    seen: set[str] = set()
    for screen in doc.get("screens", []) or []:
        if not isinstance(screen, dict):
            continue
        ref_path = str(screen.get("ocr") or "").replace("\\", "/").strip()
        screen_id = str(screen.get("screen_id") or "").strip()
        sources = [screen.get("regions") or []]
        sources.extend(
            ver.get("regions") or []
            for ver in screen.get("versions") or []
            if isinstance(ver, dict)
        )
        for regs in sources:
            for reg in regs or []:
                name = str((reg or {}).get("name") or "").strip()
                if not name:
                    continue
                if (reg or {}).get("has_red_dot"):
                    region_red_dot.add(name)
                if name not in seen:
                    seen.add(name)
                    regions.append(name)
                    if ref_path:
                        region_refs[name] = ref_path
                    if screen_id:
                        region_screens[name] = screen_id
    regions.sort()
    scenario_keys = sorted({p.stem for _root, p in iter_scenario_yaml_files(_REPO)})
    try:
        fsm_nodes = screen_verify_screen_names() or []
    except Exception:
        fsm_nodes = []
    return {
        "regions": regions,
        "region_refs": region_refs,
        "region_screens": region_screens,
        "region_red_dot": sorted(region_red_dot),
        "fsm_nodes": list(fsm_nodes),
        "exec_names": sorted(DSL_EXEC_REGISTRY.keys()),
        "scenario_keys": scenario_keys,
    }


# --- Catalog-wide static problems (mirrors the flow editor's stepIssues) ----

_PROBLEM_REGION_KINDS = (
    "click",
    "long_click",
    "match",
    "ocr",
    "while_match",
    "while_scroll",
)


def _is_templated(value: str) -> bool:
    """Template scenarios reference regions/keys via ``${var}`` placeholders
    that only resolve at load time — never flag those."""
    return "${" in value


def _step_problems(
    step: dict[str, Any],
    *,
    regions: set[str],
    red_dot: set[str],
    execs: set[str],
    keys: set[str],
) -> list[str]:
    out: list[str] = []
    for kind in _PROBLEM_REGION_KINDS:
        value = step.get(kind)
        if not isinstance(value, str):
            continue
        region = value.strip()
        if not region:
            out.append(f"{kind}: region not set")
        elif region not in regions and not _is_templated(region):
            out.append(f'{kind}: unknown region "{region}"')
        elif step.get("isRedDot") is not None and region in regions and region not in red_dot:
            out.append(f'isRedDot filter, but "{region}" has no has_red_dot in area')
    ps = step.get("push_scenario")
    name = ps.get("name") if isinstance(ps, dict) else ps
    if isinstance(name, str):
        name = name.strip()
        if not name:
            out.append("push_scenario: scenario key not set")
        elif name not in keys and not _is_templated(name):
            out.append(f'push_scenario: unknown scenario "{name}"')
    fn = step.get("exec")
    if isinstance(fn, str):
        fn = fn.strip()
        if fn and fn not in execs and not _is_templated(fn):
            out.append(f'exec: unknown function "{fn}"')
    return out


def _walk_step_problems(
    steps: Any,
    path: list[int],
    rel: str,
    sets: dict[str, set[str]],
    out: list[dict[str, Any]],
) -> None:
    if not isinstance(steps, list):
        return
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        p = [*path, i]
        step_key = "/".join(map(str, p))
        out.extend(
            {"rel": rel, "step": step_key, "issue": issue}
            for issue in _step_problems(step, **sets)
        )
        inner = step.get("steps")
        spec = step.get("loop") or step.get("repeat")
        if not isinstance(inner, list) and isinstance(spec, dict):
            inner = spec.get("steps")
        _walk_step_problems(inner, p, rel, sets, out)


def catalog_problems() -> list[dict[str, Any]]:
    """Static issues across every scenario YAML — unknown regions after a
    labeling rename, dangling ``push_scenario`` keys, unknown exec functions.
    Same checks the flow canvas runs per-file, swept over the whole catalog."""
    meta = editor_meta()
    sets = {
        "regions": set(meta["regions"]),
        "red_dot": set(meta["region_red_dot"]),
        "execs": set(meta["exec_names"]),
        "keys": set(meta["scenario_keys"]),
    }
    out: list[dict[str, Any]] = []
    for _root, path in iter_scenario_yaml_files(_REPO):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        rel = path.relative_to(_REPO).as_posix()
        _walk_step_problems(doc.get("steps"), [], rel, sets, out)
    out.sort(key=lambda r: (r["rel"], r["step"]))
    return out


# --- Reverse references ("who calls me") ------------------------------------


def _walk_push_refs(
    steps: Any,
    path: list[int],
    target: str,
    out: list[str],
) -> None:
    if not isinstance(steps, list):
        return
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        p = [*path, i]
        ps = step.get("push_scenario")
        name = ps.get("name") if isinstance(ps, dict) else ps
        if isinstance(name, str) and name.strip() == target:
            out.append("/".join(map(str, p)))
        inner = step.get("steps")
        spec = step.get("loop") or step.get("repeat")
        if not isinstance(inner, list) and isinstance(spec, dict):
            inner = spec.get("steps")
        _walk_push_refs(inner, p, target, out)


def _notify_events_for(target: str) -> list[str]:
    """Notification event types that enqueue this scenario directly
    (modules/notify pushes straight onto the worker queue)."""
    try:
        from modules.notify.config import EVENT_SCENARIOS
    except Exception:
        return []
    out: list[str] = []
    for game_map in EVENT_SCENARIOS.values():
        out.extend(
            event for event, key in game_map.items() if str(key).strip() == target
        )
    return sorted(set(out))


def scenario_callers(rel: str) -> dict[str, Any]:
    """Everything that can start the scenario at ``rel``: ``push_scenario``
    steps across the catalog, its own ``cron``, and notify event pushes.
    Answers "is it safe to rename/delete this?" without grep."""
    path = _path_for_rel(rel)
    target = path.stem
    callers: list[dict[str, Any]] = []
    for _root, p in iter_scenario_yaml_files(_REPO):
        if p.resolve() == path.resolve():
            continue
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        refs: list[str] = []
        _walk_push_refs(doc.get("steps"), [], target, refs)
        callers.extend(
            {
                "rel": scenario_source_label(p, _REPO),
                "stem": p.stem,
                "step": ref,
            }
            for ref in refs
        )
    callers.sort(key=lambda r: (r["rel"], r["step"]))
    try:
        own = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        own = None
    cron = str(own.get("cron") or "").strip() if isinstance(own, dict) else ""
    return {
        "callers": callers,
        "cron": cron,
        "notify_events": _notify_events_for(target),
    }
