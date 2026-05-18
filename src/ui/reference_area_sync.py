"""Keep ``area.json`` ``ocr`` paths in sync when a reference PNG is renamed on disk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from layout.area_regions import validate_unique_region_names


def _load_area_file(path: Path) -> Any:
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(raw_text)
    return json.loads(raw_text)


def _write_area_file(path: Path, doc: Any) -> None:
    if path.suffix.lower() in {".yaml", ".yml"}:
        content = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True)
    else:
        content = json.dumps(doc, indent=2) + "\n"
    path.write_text(content, encoding="utf-8")


def _ocr_path_candidates(
    ocr: str,
    *,
    repo_root: Path,
    ref_root: Path,
    references_prefix: str,
) -> list[Path]:
    """Paths that may appear in ``screens[].ocr`` for the same reference file."""

    raw = str(ocr or "").replace("\\", "/").strip()
    if not raw:
        return []
    out: list[Path] = []
    path_raw = Path(raw)
    if path_raw.is_absolute():
        out.append(path_raw)
    else:
        out.append(repo_root / path_raw)
    prefix = references_prefix.strip().rstrip("/")
    if raw.startswith(f"{prefix}/"):
        out.append(repo_root / raw)
    if raw.startswith("references/"):
        rel = raw.removeprefix("references/").lstrip("/")
        out.append(ref_root / rel)
    return out


def _new_ocr_after_rename(old_ocr: str, new_rel_under_refs: str, references_prefix: str) -> str:
    """Preserve module-local ``references/…`` style when the entry used it."""

    old = str(old_ocr or "").replace("\\", "/").strip()
    new_rel = Path(new_rel_under_refs).as_posix()
    prefix = references_prefix.strip().rstrip("/")
    if old.startswith("references/") and not old.startswith(f"{prefix}/"):
        return f"references/{new_rel}"
    return f"{prefix}/{new_rel}"


def sync_area_json_ocr_after_reference_rename(
    repo_root: Path,
    *,
    old_rel_under_refs: str,
    new_rel_under_refs: str,
    area_path: Path | None = None,
    references_prefix: str = "references",
) -> tuple[bool, str, int]:
    """Rewrite ``screens[].ocr`` entries that pointed at the old PNG.

    Paths are relative to the active references tree (e.g. ``city/foo.png``).

    Returns ``(ok, error_message, entries_updated)``. ``error_message`` is empty on success.
    """
    area_path = area_path or (repo_root / "area.json")
    if not area_path.is_file():
        return True, "", 0

    try:
        original_raw = _load_area_file(area_path)
    except (json.JSONDecodeError, yaml.YAMLError, OSError) as exc:
        return False, str(exc), 0

    if isinstance(original_raw, list):
        screens: list = original_raw
        validate_doc: dict = {"screens": screens}
    elif isinstance(original_raw, dict):
        scr = original_raw.get("screens")
        if not isinstance(scr, list):
            return False, "area.json: missing or invalid screens", 0
        screens = scr
        validate_doc = original_raw
    else:
        return False, "area.json: invalid structure", 0

    prefix = references_prefix.strip().rstrip("/")
    ref_root = (repo_root / prefix).resolve()
    try:
        old_abs = (ref_root / Path(old_rel_under_refs)).resolve()
    except OSError as exc:
        return False, str(exc), 0

    updated = 0
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("ocr") or "").strip()
        if not raw:
            continue
        matched = False
        for cand in _ocr_path_candidates(
            raw,
            repo_root=repo_root,
            ref_root=ref_root,
            references_prefix=prefix,
        ):
            try:
                if cand.resolve() == old_abs:
                    matched = True
                    break
            except OSError:
                continue
        if not matched:
            continue
        entry["ocr"] = _new_ocr_after_rename(raw, new_rel_under_refs, prefix)
        updated += 1

    try:
        validate_unique_region_names(validate_doc)
    except ValueError as exc:
        return False, str(exc), updated

    try:
        _write_area_file(area_path, original_raw)
    except OSError as exc:
        return False, str(exc), updated

    return True, "", updated
