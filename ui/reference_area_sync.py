"""Keep ``area.json`` ``ocr`` paths in sync when a reference PNG is renamed on disk."""

from __future__ import annotations

import json
from pathlib import Path

from layout.area_regions import validate_unique_region_names


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
        original_raw = json.loads(area_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
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

    new_ocr = f"{prefix}/{Path(new_rel_under_refs).as_posix()}".replace("\\", "/")

    updated = 0
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("ocr") or "").strip()
        if not raw:
            continue
        path_raw = Path(raw.replace("\\", "/"))
        cand = path_raw if path_raw.is_absolute() else repo_root / path_raw
        try:
            if cand.resolve() == old_abs:
                entry["ocr"] = new_ocr
                updated += 1
        except OSError:
            continue

    try:
        validate_unique_region_names(validate_doc)
    except ValueError as exc:
        return False, str(exc), updated

    try:
        area_path.write_text(json.dumps(original_raw, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, str(exc), updated

    return True, "", updated
