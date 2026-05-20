"""Load core ``area.json`` plus optional module-local area manifests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from config.module_discovery import iter_module_area_manifests


def default_area_json_path(repo_root: Path) -> Path:
    """Canonical core area manifest path."""

    return repo_root / "area.json"


def _load_area_mapping(path: Path) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            raw = yaml.safe_load(raw_text)
    else:
        raw = yaml.safe_load(raw_text)
    if isinstance(raw, list):
        return {"screens": raw}
    return raw if isinstance(raw, dict) else {}


def _module_repo_rel(repo_root: Path, module_root: Path, value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    path = Path(raw)
    if path.is_absolute() or raw.startswith("modules/"):
        return raw
    return (module_root.relative_to(repo_root) / path).as_posix()


def _normalize_module_area_doc(
    doc: dict[str, Any],
    *,
    repo_root: Path,
    module_root: Path,
) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    screens = out.get("screens")
    if not isinstance(screens, list):
        return out
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        ocr = screen.get("ocr")
        if isinstance(ocr, str):
            screen["ocr"] = _module_repo_rel(repo_root, module_root, ocr)
        versions = screen.get("versions")
        if not isinstance(versions, list):
            continue
        for version in versions:
            if not isinstance(version, dict):
                continue
            version_ocr = version.get("ocr")
            if isinstance(version_ocr, str):
                version["ocr"] = _module_repo_rel(repo_root, module_root, version_ocr)
    return out


def load_area_doc(repo_root: Path, area_path: Path | None = None) -> dict[str, Any]:
    """Load merged area configuration.

    The core manifest remains ``area.json``. Modules may add
    ``modules/<id>/area.yaml`` (or ``.yml`` / ``.json``). Module-local OCR
    references are interpreted relative to the module root and normalized to a
    repository-relative path before runtime lookup.
    """
    repo_root = repo_root.resolve()
    path = area_path or default_area_json_path(repo_root)
    if not path.is_file():
        return {}

    merged = _load_area_mapping(path)
    merged.pop("fsm", None)
    screens = merged.get("screens")
    if not isinstance(screens, list):
        screens = []
        merged["screens"] = screens

    if area_path is not None and path.resolve() != default_area_json_path(repo_root).resolve():
        return merged

    for module_area in iter_module_area_manifests(repo_root):
        module_root = module_area.parent
        module_doc = _load_area_mapping(module_area)
        module_doc = _normalize_module_area_doc(
            module_doc,
            repo_root=repo_root,
            module_root=module_root,
        )
        module_screens = module_doc.get("screens")
        if isinstance(module_screens, list):
            screens.extend(module_screens)
    return merged
