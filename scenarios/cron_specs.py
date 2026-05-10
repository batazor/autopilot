"""Cron vs plain scenarios: any YAML under ``scenarios/`` with a non-empty root ``cron`` field.

There is no separate ``by_cron/`` convention — location is arbitrary; scheduling is
determined only by the presence of ``cron`` in the parsed root mapping.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _is_under_drafts(rel_parts: tuple[str, ...]) -> bool:
    return any(p.lower() == "drafts" for p in rel_parts)


def iter_scenarios_yaml_paths(scenarios_root: Path) -> list[Path]:
    """All ``*.yaml`` under ``scenarios/``, excluding anything under ``drafts/``."""
    if not scenarios_root.is_dir():
        return []
    out: list[Path] = []
    for p in scenarios_root.rglob("*.yaml"):
        rel = p.relative_to(scenarios_root)
        if _is_under_drafts(tuple(rel.parts)):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def load_root_mapping(path: Path) -> dict[str, object] | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
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
