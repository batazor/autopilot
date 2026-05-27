"""Load merged area configuration from per-module ``area.yaml`` manifests."""
from __future__ import annotations

import copy
import json
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.module_discovery import iter_module_area_manifests


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
    from config.games import is_module_reference

    raw = value.strip()
    if not raw:
        return raw
    path = Path(raw)
    if path.is_absolute() or is_module_reference(raw):
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


def area_manifest_max_mtime(repo_root: Path, *, game: str | None = None) -> float:
    """Latest mtime across every per-module ``area.*`` manifest."""
    repo_root = repo_root.resolve()
    mtimes: list[float] = []
    for module_area in iter_module_area_manifests(repo_root, game=game):
        if module_area.is_file():
            with suppress(OSError):
                mtimes.append(float(module_area.stat().st_mtime))
    # Transitional: Phase 3 removed root ``area.json`` from production, but
    # the test suite still writes a root file in ~40 fixtures. Reading it as
    # a fallback keeps those tests green without forcing a mass migration.
    # Production code paths never put an ``area.json`` at the repo root, so
    # this branch is effectively a no-op outside of tests.
    legacy_root = repo_root / "area.json"
    if legacy_root.is_file():
        with suppress(OSError):
            mtimes.append(float(legacy_root.stat().st_mtime))
    return max(mtimes) if mtimes else 0.0


def clear_area_doc_cache() -> None:
    """Drop cached area manifests (tests, hot reload)."""
    _load_area_doc_cached.cache_clear()


def load_area_doc(repo_root: Path, *, game: str | None = None) -> dict[str, Any]:
    """Load merged area configuration from per-module ``area.yaml`` manifests.

    Module-local OCR references are interpreted relative to the module root and
    normalized to a repository-relative path before runtime lookup.
    """
    from config.games import default_game

    repo_root = repo_root.resolve()
    g = (game or default_game()).strip()
    return _load_area_doc_cached(
        str(repo_root), area_manifest_max_mtime(repo_root, game=g), g
    )


@lru_cache(maxsize=64)
def _load_area_doc_cached(
    repo_root_s: str,
    fingerprint: float,
    game: str,
) -> dict[str, Any]:
    # fingerprint is part of the cache key; file edits invalidate automatically.
    _ = fingerprint
    repo_root = Path(repo_root_s)
    merged: dict[str, Any] = {"version": 2, "screens": []}
    screens: list[Any] = merged["screens"]

    for module_area in iter_module_area_manifests(repo_root, game=game):
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

    # Transitional legacy ``area.json`` at the repo root — see comment in
    # :func:`area_manifest_max_mtime`. Production deployments never have one;
    # this only fires for tests that haven't migrated their fixtures yet.
    legacy_root = repo_root / "area.json"
    if legacy_root.is_file():
        legacy_doc = _load_area_mapping(legacy_root)
        legacy_screens = legacy_doc.get("screens")
        if isinstance(legacy_screens, list):
            screens.extend(legacy_screens)
    return merged
