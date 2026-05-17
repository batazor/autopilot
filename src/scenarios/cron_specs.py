"""Cron vs plain scenarios: any runnable YAML with a non-empty root ``cron`` field.

There is no separate ``by_cron/`` convention — location is arbitrary; scheduling is
determined only by the presence of ``cron`` in the parsed root mapping.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from config.module_registry import ALL_MODULES_KEY
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY
from scenarios.registry import iter_scenario_yaml_files, scenario_roots


def resolve_cron_priority(raw: object) -> int:
    """Coerce a cron YAML ``priority`` to an int, falling back to the unified
    :data:`scenarios.dsl_schema.DEFAULT_SCENARIO_PRIORITY` when missing.

    Cron has no enqueue-path of its own — it just feeds the same queue as
    overlay pushes and the per-task DSL constructor, so it shares the same
    default. Handles three foot-guns of the previous ``int(raw.get("priority")
    or 1)`` idiom: ``None`` (missing field), ``bool`` (``True``/``False`` are
    ints in Python — silently passed as 0 or 1), and bad string values. ``0``
    is a valid explicit priority distinct from "missing" and is preserved.
    """
    if raw is None or isinstance(raw, bool):
        return DEFAULT_SCENARIO_PRIORITY
    try:
        if isinstance(raw, (int, float, str, bytes, bytearray)):
            return int(raw)
        return DEFAULT_SCENARIO_PRIORITY
    except (TypeError, ValueError):
        return DEFAULT_SCENARIO_PRIORITY


def resolve_cron_task_type(raw: dict[str, object], path: Path) -> str:
    """Effective ``task_type`` for a cron YAML — same convention the scheduler uses.

    Resolution order (single source of truth between scheduler and UI):

    1. Explicit ``task:`` on the YAML root (preferred — the override knob).
    2. Legacy ``task_type:`` alias.
    3. The YAML file stem (``check_main_city.yaml`` → ``check_main_city``).

    The stem fallback is what most cron YAMLs in the repo rely on — they
    declare ``cron:`` and steps but no explicit ``task:``. Without this
    helper, the UI's Cron Push panel rendered an empty ``Task`` cell and
    refused to enqueue, while the scheduler (which does its own fallback)
    happily ran the same file every minute. The two paths must agree.
    """
    for k in ("task", "task_type"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return path.stem


def _is_under_drafts(rel_parts: tuple[str, ...]) -> bool:
    return any(p.lower() == "drafts" for p in rel_parts)


def iter_scenarios_yaml_paths(scenarios_root: Path) -> list[Path]:
    """All ``*.yaml`` under one scenario root, excluding anything under ``drafts/``."""
    if not scenarios_root.is_dir():
        return []
    out: list[Path] = []
    for p in scenarios_root.rglob("*.yaml"):
        rel = p.relative_to(scenarios_root)
        if _is_under_drafts(tuple(rel.parts)):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def iter_scenarios_yaml_paths_for_repo(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[Path]:
    """All scenario YAML paths under module roots for ``module_scope``."""
    return [path for _root, path in iter_scenario_yaml_files(repo_root, module_scope)]


def load_root_mapping(path: Path) -> dict[str, object] | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except yaml.YAMLError:
        return None
    return raw if isinstance(raw, dict) else None


def is_cron_schedule_doc(raw: dict[str, object]) -> bool:
    return bool(str(raw.get("cron") or "").strip())


def iter_cron_yaml_files(scenarios_root: Path) -> list[Path]:
    """YAML files whose root document defines a ``cron`` schedule."""
    out: list[Path] = []
    for p in iter_scenarios_yaml_paths(scenarios_root):
        raw = load_root_mapping(p)
        if raw is None:
            continue
        if is_cron_schedule_doc(raw):
            out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def iter_cron_yaml_files_for_repo(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[Path]:
    """Cron YAMLs across module scenario trees."""
    out: list[Path] = []
    for p in iter_scenarios_yaml_paths_for_repo(repo_root, module_scope):
        raw = load_root_mapping(p)
        if raw is None:
            continue
        if is_cron_schedule_doc(raw):
            out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def iter_plain_scenario_yaml_files(scenarios_root: Path) -> list[Path]:
    """Executable scenario YAMLs not registered via root ``cron`` (main Scenarios UI tab).

    Parse failures are included here so the UI can surface load errors instead of hiding the file.
    """
    out: list[Path] = []
    for p in iter_scenarios_yaml_paths(scenarios_root):
        raw = load_root_mapping(p)
        if raw is None:
            out.append(p)
            continue
        if is_cron_schedule_doc(raw):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def iter_plain_scenario_yaml_files_for_repo(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[Path]:
    """Plain (non-cron) scenario YAMLs across module scenario trees."""
    out: list[Path] = []
    for p in iter_scenarios_yaml_paths_for_repo(repo_root, module_scope):
        raw = load_root_mapping(p)
        if raw is None:
            out.append(p)
            continue
        if is_cron_schedule_doc(raw):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def scenario_loader_paths(repo_root: Path) -> list[Path]:
    """Directories the scheduler ``ScenarioLoader`` should watch and load."""
    return [root.path for root in scenario_roots(repo_root, ALL_MODULES_KEY)]
