"""Scaffold a module per ``references/events/event.<name>.png`` and move the
PNG into ``modules/events/<name>/references/logo.png``.

Run once via ``uv run python scripts/migrate_event_icons.py``.

For each event PNG:
- Creates ``modules/events/<name>/`` if missing (with a minimal ``module.yaml``).
- Ensures ``references/`` exists.
- Moves the PNG to ``modules/events/<name>/references/logo.png`` (no overwrite).

Also moves the two events screen entries from root ``area.json``
(``event.weekly_benefits``, ``event.crystal_reactivation``) to each module's
``area.yaml`` so the merged area manifest keeps finding them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "src"))

from config.games import default_game, modules_root_for  # noqa: E402

SRC_DIR = REPO / "references" / "events"
DST_BASE = modules_root_for(default_game(), repo_root=REPO) / "events"
ROOT_AREA = REPO / "area.json"


def _title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").title()


def _ensure_module(slug: str) -> Path:
    mod = DST_BASE / slug
    mod.mkdir(parents=True, exist_ok=True)
    refs = mod / "references"
    refs.mkdir(parents=True, exist_ok=True)
    manifest = mod / "module.yaml"
    if not manifest.is_file():
        manifest.write_text(
            "\n".join(
                [
                    f"id: {slug}",
                    f"title: {_title_from_slug(slug)}",
                    f"description: Limited-time {_title_from_slug(slug)} event.",
                    "references: references",
                    f"icon: {slug}",
                    "wiki: false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return mod


def _move_logo(slug: str, src_png: Path) -> bool:
    mod = _ensure_module(slug)
    dst = mod / "references" / "logo.png"
    if dst.exists():
        return False
    shutil.move(str(src_png), str(dst))
    return True


def _move_area_entry(slug: str, area_doc: dict[str, Any], screen: dict[str, Any]) -> None:
    """Move an area.json screen entry into the module's area.yaml."""
    mod = _ensure_module(slug)
    area_path = mod / "area.yaml"
    if area_path.is_file():
        existing = json.loads(area_path.read_text(encoding="utf-8") or "{}")
    else:
        existing = {"version": 2, "screens": []}
    existing.setdefault("screens", [])
    screen = dict(screen)
    # Rewrite ocr path to module-relative (loader rebases bare "references/...").
    screen["ocr"] = "references/logo.png"
    existing["screens"].append(screen)
    area_path.write_text(
        json.dumps(existing, indent=2) + "\n", encoding="utf-8"
    )
    # Drop from root area.json
    screens = area_doc.get("screens") or []
    area_doc["screens"] = [
        s for s in screens if s.get("id") != screen.get("id")
    ]


def main() -> None:
    if not SRC_DIR.is_dir():
        print(f"{SRC_DIR} is missing — nothing to do")
        return

    pngs = sorted(SRC_DIR.glob("event.*.png"))
    moved = 0
    created = 0
    existed = 0

    # Pre-collect area.json entries keyed by stem (event.<slug>)
    area_doc = json.loads(ROOT_AREA.read_text(encoding="utf-8"))
    screens = list(area_doc.get("screens") or [])
    screens_by_stem: dict[str, dict[str, Any]] = {}
    for s in screens:
        ocr = str(s.get("ocr") or "").replace("\\", "/")
        if ocr.startswith("references/events/event.") and ocr.endswith(".png"):
            stem = Path(ocr).stem  # "event.<slug>"
            screens_by_stem[stem] = s

    for png in pngs:
        stem = png.stem  # event.<slug>
        if not stem.startswith("event."):
            continue
        slug = stem[len("event.") :]
        mod_existed = (DST_BASE / slug).is_dir()
        if _move_logo(slug, png):
            moved += 1
            if mod_existed:
                existed += 1
                print(f"+ logo for existing module: {slug}")
            else:
                created += 1
                print(f"+ scaffolded module: {slug}")
        else:
            print(f"= skip {slug}: logo.png already present")
        # If area.json had a screen for this PNG, migrate it too
        screen = screens_by_stem.get(stem)
        if screen is not None:
            _move_area_entry(slug, area_doc, screen)
            print(f"  · moved area entry id={screen.get('id')} -> {slug}/area.yaml")

    ROOT_AREA.write_text(json.dumps(area_doc, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"moved {moved} PNGs ({created} new modules, {existed} existing)")
    remaining = list(SRC_DIR.glob("*"))
    if not remaining:
        SRC_DIR.rmdir()
        print(f"removed empty {SRC_DIR}")
    else:
        print(f"left {len(remaining)} files in {SRC_DIR}: {[p.name for p in remaining[:5]]}")


if __name__ == "__main__":
    main()
