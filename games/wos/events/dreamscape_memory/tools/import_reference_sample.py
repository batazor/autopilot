#!/usr/bin/env python3
"""One-shot importer: load the reference-sample scenes into the scene DB.

Unlike :mod:`import_maps` (community catalog + names-only sheets, ``source_image
= ""``), this imports scenes that ship with a **real 720x1280 screenshot** —
ported from the legacy ``whiteout-references/Dreamscape`` bot as committed
fixtures (``tests/fixtures/*_scene.json`` + ``references/maps/*.png``):

- ``reference-sample`` — 34 **verified full-frame tap coordinates** (hand-mapped);
  points are direct game-frame percentages (``scene_rect = null``), tapped as-is.
  This is the ground truth behind the solver round-trip test.
- ``reference-sample-2`` / ``-3`` — image only, **no coordinates yet**. They give
  the operator a real screenshot to map in the onboarding UI ("Detect numbers
  (OCR)" / drag pins).

    uv run python games/wos/events/dreamscape_memory/tools/import_reference_sample.py [--dry-run]

Every scene imports with ``activate=False`` — it never steals the operator's
active scene.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import dreamscape_db

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> None:
    dry = "--dry-run" in sys.argv
    fixtures = sorted(_FIXTURE_DIR.glob("*_scene.json"))
    if not fixtures:
        print(f"no *_scene.json fixtures under {_FIXTURE_DIR}")
        return

    for path in fixtures:
        scene = json.loads(path.read_text(encoding="utf-8"))
        points = scene["points"]
        kind = "image only" if not points else f"{len(points)} pts"
        print(
            f"{'DRY ' if dry else ''}{scene['slug']:18s} {kind:11s} · "
            f"image={scene['source_image']} · rect={scene['scene_rect']}"
        )
        if dry:
            continue
        dreamscape_db.upsert_scene(
            scene["slug"],
            title=scene["title"],
            source_image=scene["source_image"],
            scene_rect=scene["scene_rect"],
            points=points,
            activate=False,  # never steal the active pointer
        )

    if dry:
        print(f"\n(dry run) {len(fixtures)} scene(s) would be imported.")
        return
    listed = dreamscape_db.list_scenes()
    print(
        f"\nimported {len(fixtures)} scene(s). DB now holds "
        f"{len(listed['scenes'])} scene(s); active = {listed['active'] or '(none)'!r}"
    )


if __name__ == "__main__":
    main()
