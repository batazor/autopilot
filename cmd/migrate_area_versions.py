"""One-shot migration: move ``_<vid>``-suffixed regions into ``versions[].regions[]``.

Old schema stored version overrides flat in the screen entry's ``regions[]`` list,
encoded by a ``_<vid>`` suffix on the region name. New schema places them in a
nested ``versions[].regions[]`` block without the suffix, alongside an optional
``removed[]`` list for regions absent in that version.

This script:

1. Reads ``area.json``.
2. For every screen entry with declared versions, walks its base ``regions[]``,
   moves each name ending with ``_<vid>`` into ``versions[V].regions[]`` with the
   suffix stripped.
3. Writes ``area.json`` back (pretty-printed, stable key order).
4. Renames matching files in ``references/crop/``: ``<stem>_<base>_<vid>.png`` →
   ``<stem>_<base>.png`` (where ``<stem>`` already encodes the version, e.g.
   ``main_city_v2``).
5. Runs ``validate_versions`` and ``validate_unique_region_names`` on the result.

Idempotent: re-running on already-migrated input is a no-op (no suffixed names
to find, no crop files matching the old pattern).

Usage:
    uv run python -m cmd.migrate_area_versions          # dry-run, prints plan
    uv run python -m cmd.migrate_area_versions --apply  # actually mutates files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AREA_JSON = REPO_ROOT / "area.json"
CROPS_DIR = REPO_ROOT / "references" / "crop"


def _declared_version_ids(entry: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ver in entry.get("versions") or []:
        if not isinstance(ver, dict):
            continue
        vid = str(ver.get("id", "") or "").strip()
        if vid:
            out.append(vid)
    return out


def _split_suffix(name: str, declared: list[str]) -> tuple[str, str | None]:
    """Return (base_name, version_id) if name ends with one of the declared ``_vN``, else (name, None)."""
    for vid in sorted(declared, key=len, reverse=True):
        suffix = f"_{vid}"
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], vid
    return name, None


def _ocr_stem(rel: str) -> str:
    return Path(rel).stem if rel else ""


def plan_migration(doc: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[Path, Path]], list[str]]:
    """Return (new_doc, crop_renames, notes) without touching the filesystem."""
    notes: list[str] = []
    crop_renames: list[tuple[Path, Path]] = []
    new_doc: dict[str, Any] = json.loads(json.dumps(doc))

    for entry in new_doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        declared = _declared_version_ids(entry)
        if not declared:
            continue

        ver_blocks: dict[str, dict[str, Any]] = {}
        for ver in entry.get("versions") or []:
            if isinstance(ver, dict):
                vid = str(ver.get("id", "") or "").strip()
                if vid:
                    ver_blocks[vid] = ver

        regions = entry.get("regions") or []
        if not isinstance(regions, list):
            continue

        kept: list[dict[str, Any]] = []
        for reg in regions:
            if not isinstance(reg, dict):
                kept.append(reg)
                continue
            name = str(reg.get("name", "") or "").strip()
            base, vid = _split_suffix(name, declared)
            if vid is None:
                kept.append(reg)
                continue

            ver_block = ver_blocks.get(vid)
            if ver_block is None:
                notes.append(
                    f"  ! orphan: region {name!r} on {entry.get('screen_id')!r} has suffix "
                    f"_{vid} but no matching version block — leaving in base"
                )
                kept.append(reg)
                continue

            ver_regions = ver_block.setdefault("regions", [])
            if any(
                isinstance(x, dict) and str(x.get("name", "") or "").strip() == base
                for x in ver_regions
            ):
                notes.append(
                    f"  ! conflict: {entry.get('screen_id')!r} version {vid} already has "
                    f"a region named {base!r} — dropping {name!r}"
                )
                continue

            new_reg = dict(reg)
            new_reg["name"] = base
            ver_regions.append(new_reg)
            notes.append(f"  · {entry.get('screen_id')!r}: {name!r} → versions[{vid}].regions[].{base!r}")

            default_ocr = str(entry.get("ocr") or "").strip()
            ver_ocr = str(ver_block.get("ocr") or "").strip() or default_ocr
            stem = _ocr_stem(ver_ocr)
            if stem:
                old_crop = CROPS_DIR / f"{stem}_{name}.png"
                new_crop = CROPS_DIR / f"{stem}_{base}.png"
                if old_crop != new_crop:
                    crop_renames.append((old_crop, new_crop))

        entry["regions"] = kept

    return new_doc, crop_renames, notes


def apply_migration(*, dry_run: bool) -> int:
    if not AREA_JSON.is_file():
        print(f"!! {AREA_JSON} not found", file=sys.stderr)
        return 2

    raw = json.loads(AREA_JSON.read_text(encoding="utf-8"))
    new_doc, crop_renames, notes = plan_migration(raw)

    print(f"== migration plan for {AREA_JSON.relative_to(REPO_ROOT)} ==")
    if not notes:
        print("  (nothing to migrate — already in new shape or no versioned regions)")
    for n in notes:
        print(n)

    print()
    print(f"== crop renames ({len(crop_renames)}) ==")
    for old, new in crop_renames:
        exists = old.is_file()
        marker = "  " if exists else "?? "
        print(f"{marker}{old.relative_to(REPO_ROOT)} -> {new.relative_to(REPO_ROOT)}")
        if not exists:
            print("     (source file missing — skipping)")

    if dry_run:
        print()
        print("(dry-run; pass --apply to write changes)")
        return 0

    AREA_JSON.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"wrote {AREA_JSON.relative_to(REPO_ROOT)}")

    renamed = 0
    for old, new in crop_renames:
        if not old.is_file():
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        renamed += 1
    print(f"renamed {renamed} crop file(s)")

    sys.path.insert(0, str(REPO_ROOT))
    from layout.area_regions import validate_unique_region_names, validate_versions

    validate_versions(new_doc)
    validate_unique_region_names(new_doc)
    print("validation: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually mutate area.json + crop files")
    args = parser.parse_args()
    return apply_migration(dry_run=not args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
