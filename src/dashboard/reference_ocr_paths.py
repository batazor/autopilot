"""Resolve ``area.json`` / ``area.yaml`` ``ocr`` paths against module reference trees."""
from __future__ import annotations

from pathlib import Path

from config.paths import repo_root


def normalize_references_prefix(references_prefix: str) -> str:
    return references_prefix.replace("\\", "/").strip().strip("/") or "references"


def resolve_ocr_path_in_reference_context(
    ocr_rel: str,
    references_prefix: str,
    *,
    repo_root_path: Path | None = None,
) -> Path:
    """Map a screen ``ocr`` field to the on-disk reference PNG."""
    root = (repo_root_path or repo_root()).resolve()
    raw = ocr_rel.replace("\\", "/").strip()
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    prefix = normalize_references_prefix(references_prefix)
    if prefix != "references" and raw.startswith("references/"):
        return (root / prefix / raw.removeprefix("references/")).resolve()
    return (root / raw).resolve()


def module_local_ocr_for_reference_path(ocr_repo_rel: str, references_prefix: str) -> str:
    """Store module screens as ``references/<file>.png`` when the tree is under ``modules/``."""
    ocr_norm = ocr_repo_rel.replace("\\", "/").strip()
    prefix = normalize_references_prefix(references_prefix)
    if prefix != "references" and ocr_norm.startswith(f"{prefix}/"):
        return f"references/{ocr_norm.removeprefix(f'{prefix}/')}"
    return ocr_norm


def reference_basename_stem(rel_under_refs: str) -> str:
    """Basename without ``.png`` for a path relative to the active references root."""
    rel = rel_under_refs.replace("\\", "/").strip().strip("/")
    if not rel:
        return ""
    p = Path(rel)
    if len(p.parts) > 1:
        return str(p.with_suffix("")).replace("\\", "/")
    return p.stem
