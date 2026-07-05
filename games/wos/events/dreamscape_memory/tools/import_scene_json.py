#!/usr/bin/env python3
"""Import a community-labeled Dreamscape scene JSON into the scene DB.

The JSON comes from the docs-site online label editor
(https://batazor.github.io/autopilot-page/authoring/label-editor/) — a user
drops a scene screenshot in the browser, places named points, and exports:

    {
      "type": "dreamscape_scene",
      "slug": "frost-harbor",
      "title": "Frost Harbor",
      "season": 3,
      "image": "frost-harbor.png",
      "points": [{"n": 1, "name": "Book", "xPct": 32.59, "yPct": 38.93}, ...]
    }

This tool validates the payload, optionally installs the screenshot under
``references/maps/<slug>/``, and upserts via ``dreamscape_db.upsert_scene``.
Existing scenes are updated in place; the active pointer is never stolen
unless ``--activate`` is passed.

    uv run python games/wos/events/dreamscape_memory/tools/import_scene_json.py \
        frost-harbor.json --image frost-harbor.png [--activate] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from config import dreamscape_db
from config.paths import repo_root

_MAPS_DIR = Path("games/wos/events/dreamscape_memory/references/maps")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _validate(data: dict) -> tuple[str, str, int, list[dict]]:
    if data.get("type") not in (None, "dreamscape_scene"):
        _fail(f"not a dreamscape scene export (type={data.get('type')!r}); "
              "zones/regions JSONs are applied to area.yaml by hand")
    slug = str(data.get("slug", "")).strip()
    if not _SLUG_RE.match(slug):
        _fail(f"bad or missing slug {slug!r} (want kebab-case, e.g. frost-harbor)")
    title = str(data.get("title", "")).strip() or slug.replace("-", " ").title()
    try:
        season = int(data.get("season", 1))
    except (TypeError, ValueError):
        _fail(f"season must be an integer, got {data.get('season')!r}")

    raw = data.get("points")
    if not isinstance(raw, list) or not raw:
        _fail("no points in the payload")
    points: list[dict] = []
    seen_n: set[int] = set()
    seen_names: set[str] = set()
    for i, p in enumerate(raw):
        try:
            n = int(p["n"])
            name = " ".join(str(p["name"]).split())
            x, y = float(p["xPct"]), float(p["yPct"])
        except (KeyError, TypeError, ValueError):
            _fail(f"point #{i}: expected {{n, name, xPct, yPct}}, got {p!r}")
        if not name:
            _fail(f"point n={n}: empty name — every point needs the in-game word")
        if not (0 <= x <= 100 and 0 <= y <= 100):
            _fail(f"point n={n} ({name}): coordinates out of 0–100% range ({x}, {y})")
        if n in seen_n:
            _fail(f"duplicate point number n={n}")
        key = name.lower()
        if key in seen_names:
            # The solver keys taps by word — a scene can't hold two positions
            # under one name.
            _fail(f"duplicate item name {name!r} (n={n})")
        seen_n.add(n)
        seen_names.add(key)
        points.append({"n": n, "name": name, "xPct": round(x, 2), "yPct": round(y, 2)})
    points.sort(key=lambda p: p["n"])
    return slug, title, season, points


def _install_image(image: Path, slug: str, dry: bool) -> str:
    dest = _MAPS_DIR / slug / f"{slug}{image.suffix.lower()}"
    print(f"{'DRY ' if dry else ''}image  {image} → {dest}")
    if not dry:
        full = repo_root() / dest
        full.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, full)
    return str(dest)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json_path", type=Path, help="exported scene JSON from the online editor")
    ap.add_argument("--image", type=Path, help="the scene screenshot to install under references/maps/<slug>/")
    ap.add_argument("--activate", action="store_true", help="make this the active scene")
    ap.add_argument("--archived", action="store_true", help="import as archived (not in current rotation)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        data = json.loads(args.json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read {args.json_path}: {exc}")
    slug, title, season, points = _validate(data)

    existing = dreamscape_db.get_scene(slug)
    source_image = existing["source_image"] if existing else ""
    if args.image:
        if not args.image.is_file():
            _fail(f"image not found: {args.image}")
        source_image = _install_image(args.image, slug, args.dry_run)
    elif existing is None:
        print("note: no --image given; scene imports without a reference screenshot")

    verb = "update" if existing else "create"
    print(
        f"{'DRY ' if args.dry_run else ''}{verb} {slug!r} · {title!r} · season {season} · "
        f"{len(points)} point(s)"
        + (" · ACTIVATE" if args.activate else "")
        + (" · archived" if args.archived else "")
    )
    if args.dry_run:
        return
    dreamscape_db.upsert_scene(
        slug,
        title=title,
        source_image=source_image,
        scene_rect=existing["scene_rect"] if existing else None,
        points=points,
        activate=args.activate,
        archived=True if args.archived else (None if existing else False),
        season=season,
    )
    listed = dreamscape_db.list_scenes()
    print(
        f"done — DB holds {len(listed['scenes'])} scene(s); active = "
        f"{listed['active'] or '(none)'!r}"
    )


if __name__ == "__main__":
    main()
