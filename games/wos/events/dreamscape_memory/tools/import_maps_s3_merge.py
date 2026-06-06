#!/usr/bin/env python3
"""**Merge-only** import of new Season 3 items into the scene DB.

Unlike :mod:`import_maps_s3` (which fully *replaces* every scene's points), this
tool preserves existing pins — including operator-adjusted positions from the
Live editor — and only **appends items that are new** (matched by item *number*
``n``, so name typos/synonyms in the DB don't spawn duplicate pins).

New items get their position from OCR of the printed marker numbers on the guide
image (same multi-pass OCR as the base importer); numbers OCR can't read fall
back to a placeholder grid for manual dragging. Existing scenes' ``source_image``,
``scene_rect``, ``season``, ``archived`` and ``active`` flags are left untouched.

    uv run python games/wos/events/dreamscape_memory/tools/import_maps_s3_merge.py [--dry-run] [--no-ocr]
"""
from __future__ import annotations

import sys

# Reuse the base importer's sheet parsing, guide-image extraction and OCR.
import import_maps_s3 as base

from config import dreamscape_db


def main() -> None:
    dry = "--dry-run" in sys.argv
    ocr = "--no-ocr" not in sys.argv

    maps = base._parse_maps(base._fetch(base.CSV_URL))
    images = base._guide_images(base._fetch(base.XLSX_URL))
    print(f"parsed {len(maps)} map(s), {len(images)} guide image(s)")
    if len(images) != len(maps):
        sys.exit(
            f"abort: {len(images)} guide images != {len(maps)} maps — the sheet "
            "layout changed; re-check the column/row pairing before importing."
        )

    grand_new = grand_placed = 0
    for (title, items), img in zip(maps, images, strict=False):
        slug = f"{base._slugify(title)}-s3"
        scene = dreamscape_db.get_scene(slug)
        if scene is None:
            print(f"  SKIP {slug}: scene not found (use import_maps_s3.py to create it)")
            continue

        existing = list(scene["points"])
        existing_nums = {p.get("n") for p in existing}
        new_items = [(n, name) for n, name in items if n not in existing_nums]
        if not new_items:
            print(f"{slug:22s} {len(existing):3d} pts · no new items")
            continue

        # Placeholder grid for the new items, then snap to OCR'd marker numbers.
        new_points = base._grid_points(new_items)
        pos = (
            base._ocr_positions(img, max(n for n, _ in new_items))
            if ocr and not dry
            else {}
        )
        placed = 0
        for p in new_points:
            if p["n"] in pos:
                p["xPct"], p["yPct"] = pos[p["n"]]
                placed += 1

        merged = existing + new_points
        grand_new += len(new_points)
        grand_placed += placed
        nums = ", ".join(f"#{n} {name}" for n, name in new_items)
        ocr_note = f" · OCR placed {placed}/{len(new_points)}" if ocr and not dry else ""
        print(
            f"{'DRY ' if dry else ''}{slug:22s} {len(existing):3d}→{len(merged):3d} pts "
            f"(+{len(new_points)}){ocr_note}\n      new: {nums}"
        )
        if not dry:
            dreamscape_db.upsert_scene(
                slug,
                title=scene["title"],
                source_image=scene["source_image"],
                scene_rect=scene["scene_rect"],
                points=merged,
                activate=scene["active"],
                archived=None,  # preserve rotation tagging
                season=None,  # preserve season
                images=scene["images"] or None,
            )

    print(
        f"\n{'(dry run) ' if dry else ''}added {grand_new} new point(s)"
        + (f", {grand_placed} OCR-placed" if ocr and not dry else "")
        + "."
    )


if __name__ == "__main__":
    main()
