#!/usr/bin/env python3
"""One-shot importer: load the **Monument** (Recall Road) multiplayer scene.

The three reference-sample screenshots that ship as committed fixtures
(``tests/fixtures/*_scene.json`` + ``references/maps/*.png``) are the Recall
Road co-op screen captured three ways. They are merged into a **single**
``monument`` scene (the co-op Multiplayer category) with a 3-image gallery:

- ``reference-sample`` — 34 **verified full-frame tap coordinates** (hand-mapped);
  the primary/item-mapped image and the ground truth behind the solver
  round-trip test.
- ``reference-sample-2`` / ``-3`` — extra reference shots (no coordinates), kept
  as gallery images for the operator.

    uv run python games/wos/events/dreamscape_memory/tools/import_reference_sample.py [--dry-run]

Imports with ``activate=False`` (never steals the active scene) and
``season = SEASON_MULTIPLAYER``. Replaces the legacy per-shot
``reference-sample[-N]`` scenes if they still exist.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from config import dreamscape_db

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_MONUMENT_SLUG = "monument"
_MONUMENT_TITLE = "Monument"
# Legacy per-shot scenes folded into ``monument`` (deleted on import).
_LEGACY_SLUGS = ("reference-sample", "reference-sample-2", "reference-sample-3")


def _sample_order(path: Path) -> int:
    """Sort key by trailing shot number (base → 1, ``..._2_scene`` → 2)."""
    m = re.search(r"[-_](\d+)_scene\.json$", path.name)
    return int(m.group(1)) if m else 1


def main() -> None:
    dry = "--dry-run" in sys.argv
    fixtures = sorted(_FIXTURE_DIR.glob("*_scene.json"), key=_sample_order)
    if not fixtures:
        print(f"no *_scene.json fixtures under {_FIXTURE_DIR}")
        return

    shots = [json.loads(p.read_text(encoding="utf-8")) for p in fixtures]
    images = [s["source_image"] for s in shots]
    # Primary = the shot carrying the hand-mapped points (the rest are extra
    # reference views with no coordinates).
    primary = max(shots, key=lambda s: len(s.get("points") or []))

    for s in shots:
        pts = s.get("points") or []
        tag = "PRIMARY" if s is primary else "gallery"
        print(
            f"{'DRY ' if dry else ''}  {tag:7s} {s['slug']:20s} "
            f"{len(pts):2d} pts · {s['source_image']}"
        )
    print(
        f"{'DRY ' if dry else ''}=> {_MONUMENT_SLUG!r} ({_MONUMENT_TITLE}) · "
        f"{len(images)} image(s) · {len(primary.get('points') or [])} pts · "
        f"season={dreamscape_db.SEASON_MULTIPLAYER} (Multiplayer)"
    )
    if dry:
        print("\n(dry run) 1 Monument scene would be imported.")
        return

    dreamscape_db.upsert_scene(
        _MONUMENT_SLUG,
        title=_MONUMENT_TITLE,
        source_image=primary["source_image"],
        images=images,
        scene_rect=primary["scene_rect"],
        points=primary.get("points") or [],
        activate=False,  # never steal the active pointer
        season=dreamscape_db.SEASON_MULTIPLAYER,
    )
    # Fold away the legacy per-shot scenes (no-op if already gone).
    for slug in _LEGACY_SLUGS:
        if slug != _MONUMENT_SLUG and dreamscape_db.delete_scene(slug):
            print(f"  removed legacy scene {slug!r}")

    listed = dreamscape_db.list_scenes()
    print(
        f"\nimported Monument ({len(images)} images). DB now holds "
        f"{len(listed['scenes'])} scene(s); active = {listed['active'] or '(none)'!r}"
    )


if __name__ == "__main__":
    main()
