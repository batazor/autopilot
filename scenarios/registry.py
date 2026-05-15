"""Scenario source registry.

Core scenarios live under ``scenarios/``. Optional feature modules can add
their own runnable YAMLs under ``modules/<module_id>/scenarios/`` without
being copied into the core tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScenarioRoot:
    """One directory that can contain scenario YAML files."""

    path: Path
    label: str
    module_id: str | None = None


def scenario_roots(repo_root: Path) -> list[ScenarioRoot]:
    """Return all enabled scenario roots in deterministic lookup order."""
    roots: list[ScenarioRoot] = []
    core = repo_root / "scenarios"
    if core.is_dir():
        roots.append(ScenarioRoot(path=core, label="scenarios"))

    modules_dir = repo_root / "modules"
    if modules_dir.is_dir():
        for module_dir in sorted(modules_dir.iterdir(), key=lambda p: p.name):
            if not module_dir.is_dir() or module_dir.name.startswith("."):
                continue
            scen_dir = module_dir / "scenarios"
            if scen_dir.is_dir():
                roots.append(
                    ScenarioRoot(
                        path=scen_dir,
                        label=f"modules/{module_dir.name}/scenarios",
                        module_id=module_dir.name,
                    )
                )
    return roots


def is_under_drafts(path: Path, root: Path) -> bool:
    """True when ``path`` is below a ``drafts`` directory inside ``root``."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part.lower() == "drafts" for part in rel.parts)


def iter_scenario_yaml_files(repo_root: Path) -> list[tuple[ScenarioRoot, Path]]:
    """All scenario YAMLs from core and modules, excluding drafts."""
    out: list[tuple[ScenarioRoot, Path]] = []
    for root in scenario_roots(repo_root):
        for path in root.path.rglob("*.yaml"):
            if is_under_drafts(path, root.path):
                continue
            out.append((root, path))
    return sorted(out, key=lambda item: (item[0].label, item[1].as_posix()))


def iter_module_analyze_manifests(repo_root: Path) -> list[Path]:
    """Module-local analyze manifests in deterministic order."""
    out: list[Path] = []
    modules_dir = repo_root / "modules"
    if not modules_dir.is_dir():
        return out
    for module_dir in sorted(modules_dir.iterdir(), key=lambda p: p.name):
        if not module_dir.is_dir() or module_dir.name.startswith("."):
            continue
        manifest = module_dir / "analyze" / "analyze.yaml"
        if manifest.is_file():
            out.append(manifest)
    return out


def iter_module_area_manifests(repo_root: Path) -> list[Path]:
    """Module-local area manifests in deterministic order."""
    out: list[Path] = []
    modules_dir = repo_root / "modules"
    if not modules_dir.is_dir():
        return out
    for module_dir in sorted(modules_dir.iterdir(), key=lambda p: p.name):
        if not module_dir.is_dir() or module_dir.name.startswith("."):
            continue
        for name in ("area.yaml", "area.yml", "area.json"):
            manifest = module_dir / name
            if manifest.is_file():
                out.append(manifest)
                break
    return out


def scenario_source_label(path: Path, repo_root: Path) -> str:
    """Human-readable source label for logs/UI, relative to its scenario root."""
    for root in scenario_roots(repo_root):
        try:
            rel = path.relative_to(root.path).as_posix()
        except ValueError:
            continue
        if root.module_id is None:
            return rel
        return f"{root.label}/{rel}"
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()
