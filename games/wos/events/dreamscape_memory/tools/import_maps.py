#!/usr/bin/env python3
"""One-shot importer: bulk-load Dreamscape scenes into the scene DB.

Two sources are merged into ``config.dreamscape_db`` (the solver's source of
truth) via :func:`dreamscape_db.upsert_scene`:

1. **Located scenes** parsed from ``web/lib/dreamscape.ts`` — community guides
   with real ``xPct``/``yPct`` per item (Garden, Mine, Hospital, Farmhouse,
   Hangar, Kitchen, Court, Arena, …). Imported with coordinates.

2. **Sheet-only maps** that the wostools catalog doesn't cover (Yard, Kid's
   Room, Windmill, …) — only names + numbers are known, so points are laid out on
   a placeholder grid for the operator to position later (via the onboarding
   "Detect numbers (OCR)" pass or by dragging pins). These names are pulled live
   from the King Shield Google Sheet (``_fetch_sheet_maps``); a bundled fallback
   (``_SHEET_FALLBACK``) covers offline runs and maps the sheet omits.

Within-scene duplicate names are collapsed (kept-first) because the solver keys
taps by name and can't hold two positions under one word. Existing scenes
(e.g. the operator's ``practice-level``) and the active pointer are preserved —
every import here uses ``activate=False``.

    uv run python games/wos/events/dreamscape_memory/tools/import_maps.py [--dry-run]
"""
from __future__ import annotations

import csv
import io
import math
import re
import sys
import urllib.request

from config import dreamscape_db
from config.paths import repo_root

_DREAMSCAPE_TS = repo_root() / "web" / "lib" / "dreamscape.ts"

# King Shield community item sheet — the upstream source of names+numbers per
# map. Pulled live (CSV export) by ``_fetch_sheet_maps``; the bundled
# ``_SHEET_FALLBACK`` below is used offline and for maps the sheet omits.
SHEET_ID = "1MzrNWOcg-QpzpdBUulC0iorv2O6dzSrFhBe7nSdjxck"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
)
# Sheet spells some titles differently from the wostools catalog / DB slugs.
_SLUG_ALIASES = {"hanger": "hangar"}
_MAP_NAME_RE = re.compile(r"MAP NAME\s*:\s*(.+)", re.IGNORECASE)
_ITEM_RE = re.compile(r"(\d+)\.\s*(.+)")

# Bundled fallback: maps present in the community sheet but NOT in the wostools
# catalog. Only names+numbers are known; coordinates come later. Single-letter /
# numeric entries are the sheet's watermark glyphs — kept verbatim. Used when the
# live fetch fails, and for maps the sheet no longer carries (Dock/Inn/Cafes).
_SHEET_FALLBACK: dict[str, tuple[str, list[tuple[int, str]]]] = {
    "yard": (
        "Yard",
        [
            (1, "Parachutte"), (2, "Envelope"), (3, "Pipe"), (4, "Airship"),
            (5, "Spring"), (6, "Shield"), (7, "Diamond"), (8, "Swan"),
            (9, "Chandelier"), (10, "Sun"), (11, "Newspaper"), (12, "Globe"),
            (13, "Windmill"), (14, "Key"), (15, "Balloon"), (16, "Bowknot"),
            (17, "Harp"), (18, "Compass"), (19, "Flag garland"), (20, "Lightning"),
            (21, "N"), (22, "Nameplate"), (23, "Clock"), (24, "Medal"),
            (26, "Butterfly"), (27, "Scroll"), (28, "Crack"), (29, "Chimney"),
            (30, "Briefcase"), (31, "Suitcase"), (32, "Camera"), (33, "Scarf"),
            (34, "Walking stick"), (35, "Giftbox"), (36, "Tree"), (37, "Twin sword"),
            (38, "Vine"), (39, "Motorcycle"), (40, "Wrench"), (41, "Gear"),
            (42, "Tower"), (43, "Star"), (44, "Searchlight"), (45, "Ladder"),
            (46, "Goggles"), (47, "Four leaf clover"), (48, "Flower"), (49, "Crow"),
            (50, "Cat"),
        ],
    ),
    "kids-room": (
        "Kid's Room",
        [
            (1, "Plant"), (2, "Rope"), (3, "Pouch"), (4, "Vent"), (5, "Frying pan"),
            (6, "Calendar"), (7, "Curtain"), (8, "Mouse"), (9, "Spider"),
            (10, "Gold coin"), (11, "Painting"), (12, "Camera"), (13, "Easter egg"),
            (14, "Diary"), (15, "Towel"), (16, "Headwear"), (17, "Key"),
            (18, "Guitar"), (19, "Spring"), (20, "Mask"), (21, "Cheese"),
            (22, "Rifle"), (23, "Cup"), (24, "Clock"), (25, "Giftbox"),
            (26, "Cloths hanger"), (27, "Outfit"), (28, "Rubber ball"), (29, "Flower"),
            (30, "Snowflake"), (31, "Bucket"), (32, "Comb"), (33, "Teddy"),
            (34, "Scroll"), (35, "Hourglass"), (36, "Candle"), (37, "Horn"),
            (38, "Hand drum"), (39, "Tree"), (40, "Slipper"), (41, "E"),
            (42, "Stain"), (43, "Goggles"), (44, "Chest"), (45, "Plate"),
            (46, "Placement"), (47, "Glass bottle"), (48, "Brush"), (49, "Wood wall art"),
        ],
    ),
    "windmill": (
        "Windmill",
        [
            (1, "Windmill"), (2, "Blueprint"), (3, "Mailbox"), (4, "Moose"),
            (5, "Wind cone"), (6, "Toolbox"), (7, "Flare"), (8, "Axe"), (9, "Rifle"),
            (10, "Watchtower"), (11, "Sign"), (12, "Horse"), (13, "Ladder"),
            (14, "Flower"), (15, "Gloves"), (16, "Snowman"), (17, "Bear trap"),
            (18, "Bird"), (19, "Pouch"), (20, "Waterbag"), (21, "Scarf"),
            (22, "Snowmobile"), (23, "Lamp"), (24, "Stool"), (25, "Bell"),
            (26, "Arrow sign"), (27, "Hook"), (28, "Shovel"), (29, "Heart"),
            (30, "Wheat"), (31, "Aeroplane"), (32, "Slingshot"), (33, "Hot air balloon"),
            (34, "Barrel"), (35, "Glass bottle"), (36, "Boomerang"), (37, "Haystack"),
            (38, "Rolled Carpet"), (39, "Headwear"), (40, "Bone"), (41, "Whip"),
            (42, "Carrot"), (43, "Triangle"), (44, "Skies"), (45, "Brush"),
            (46, "Shoes"), (47, "Hammer"), (48, "F"), (49, "Handsaw"), (50, "Apple"),
        ],
    ),
    "dock": (
        "Dock",
        [
            (1, "Cat"), (2, "Window"), (3, "Sea turtle"), (4, "Chimney"),
            (5, "Signboard"), (6, "Whale"), (7, "Parasol"), (8, "Bench"),
            (9, "Exhaust fan"), (10, "Sea horse"), (11, "Rudder"), (12, "Steak"),
            (13, "Volleyball"), (14, "Z"), (15, "Fishing rod"), (16, "Sailboat"),
            (17, "Crane"), (18, "Plane"), (19, "Backpack"), (20, "Flag"),
            (21, "Seagull"), (22, "Oxygen tank"), (23, "Harpoon"), (24, "Jar"),
            (25, "Hot air balloon"), (26, "Lifebuoy"), (27, "Drifting bottle"),
            (28, "Pouch"), (29, "Potato"), (30, "Lighthouse"), (31, "Submarine"),
            (32, "Fishing net"), (33, "Telescope"), (34, "Patch"), (35, "Pumpkin"),
            (36, "Oar"), (37, "Rubber Duck"), (38, "Octopus"), (39, "Shell"),
            (40, "Mug"), (41, "Hat"), (42, "Sea Cucumber"), (43, "Coral"),
            (44, "Goggles"), (45, "Ladder"), (46, "Lobster"), (47, "Anchor"),
            (48, "Compass"), (49, "Bread"), (50, "Crab"), (51, "Starfish"),
        ],
    ),
    "inn": (
        "Inn",
        [
            (1, "Sandwich"), (2, "Portrait"), (3, "Shoes"), (4, "Teapot"),
            (5, "Hourglass"), (6, "Pouch"), (7, "Sword"), (8, "Fork"), (9, "Moon"),
            (10, "Teddy"), (11, "Flag"), (12, "Gramophone"), (13, "Telescope"),
            (14, "Bullet"), (15, "Envelope"), (16, "Fish bone"), (17, "Bow"),
            (18, "Broom"), (19, "Saber tooth tiger"), (20, "Clock"), (21, "Chest"),
            (22, "Handgun"), (23, "Spider web"), (24, "Rose"), (25, "Water stain"),
            (26, "Bowl"), (27, "Cello"), (28, "Wheat"), (29, "Turkey"),
            (30, "Shoulder bag"), (31, "Mask"), (32, "Spatula"), (33, "Headwear"),
            (34, "Tree"), (35, "Bird"), (36, "High stool"), (37, "Magnifier"),
            (38, "Curtain"), (39, "Scratch mark"), (40, "Star or Frost Star"),
            (41, "Book"), (42, "Backpack"), (43, "Mouse"), (44, "Target"),
            (45, "Scarf"), (46, "X"), (47, "Hand fan"), (48, "Knight Headguard"),
            (49, "Mug"), (50, "Spider"),
        ],
    ),
    "cafes": (
        "Cafes",
        [
            (1, "Car"), (2, "Chimney"), (3, "Gift box"), (4, "Coffee cup"),
            (5, "Satchel"), (6, "Paw mark"), (7, "Umbrella"), (8, "Star"),
            (9, "Music note"), (10, "Lollipop"), (11, "Scarf"), (12, "Ring"),
            (13, "Key"), (14, "Fish bone"), (15, "Crow"), (16, "Glass jar"),
            (17, "Envelope"), (18, "Bread slice"), (19, "Trumpet"), (20, "Sun"),
            (21, "Fork"), (22, "Suitcase"), (23, "Goggles"), (24, "Yarn ball"),
            (25, "Fountain pen"), (26, "Diary"), (27, "Pocket watch"), (28, "Cake"),
            (29, "Apple"), (30, "Hole"), (31, "Accordion"), (32, "Hot air balloon"),
            (33, "Moon"), (34, "Rose"), (35, "Arrow sign"), (36, "Ladder"),
            (37, "Broom"), (38, "Pipe"), (39, "Butterfly"), (40, "Corn"),
            (41, "Barrel"), (42, "Headwear"), (43, "N"), (44, "Flour"), (45, "Violin"),
            (46, "123"), (47, "Stuffed bunny"), (48, "Clock"), (49, "Bell"),
            (50, "Chandelier"), (51, "Piano"), (52, "Poster"), (53, "Curtain"),
            (54, "Flag"), (55, "Branch"),
        ],
    ),
}

def _slugify(title: str) -> str:
    """Sheet ``MAP NAME`` → DB slug (apostrophes dropped, aliases applied)."""
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower().replace("'", "")).strip("-")
    return _SLUG_ALIASES.get(s, s) or "scene"


def _fetch_sheet_maps() -> dict[str, tuple[str, list[tuple[int, str]]]]:
    """Live-pull the King Shield sheet → ``{slug: (title, [(n, name), ...])}``.

    Names only (the sheet carries no coordinates). Numbers repeat across the
    sheet's two item columns, so the first name seen per number wins.
    """
    req = urllib.request.Request(SHEET_CSV_URL, headers={"User-Agent": "autopilot-import"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted URL)
        text = resp.read().decode("utf-8", "replace")
    out: dict[str, tuple[str, dict[int, str]]] = {}
    cur: str | None = None
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        header = next(
            (m.group(1).strip() for c in cells if (m := _MAP_NAME_RE.match(c))), None
        )
        if header:
            cur = header
            out.setdefault(_slugify(header), (header, {}))
            continue
        if cur is None:
            continue
        _, items = out[_slugify(cur)]
        for c in cells:
            im = _ITEM_RE.match(c)
            if im:
                items.setdefault(int(im.group(1)), im.group(2).strip())
    return {slug: (title, sorted(items.items())) for slug, (title, items) in out.items()}


_POINT_RE = re.compile(
    r'\{\s*n:\s*(\d+),\s*name:\s*"([^"]*)",\s*xPct:\s*([-\d.]+),\s*yPct:\s*([-\d.]+)'
)
_SCENE_HEAD_RE = re.compile(r'slug:\s*"([^"]+)",\s*\n?\s*title:\s*"([^"]*)"')
_ACTIVE_RE = re.compile(r"active:\s*(true|false)")


def _parse_dreamscape_ts() -> list[tuple[str, str, bool, list[tuple[int, str, float, float]]]]:
    """Return ``[(slug, title, active, [(n, name, xPct, yPct), ...]), ...]``.

    ``active`` is the wostools event-rotation flag (True = current rotation).
    """
    text = _DREAMSCAPE_TS.read_text(encoding="utf-8")
    heads = list(_SCENE_HEAD_RE.finditer(text))
    scenes: list[tuple[str, str, bool, list[tuple[int, str, float, float]]]] = []
    for i, head in enumerate(heads):
        start = head.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[start:end]
        pts = [
            (int(n), name, float(x), float(y))
            for n, name, x, y in _POINT_RE.findall(block)
        ]
        m = _ACTIVE_RE.search(block)
        active = m.group(1) == "true" if m else True
        scenes.append((head.group(1), head.group(2), active, pts))
    return scenes


def _dedupe(points: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop within-scene duplicate names (keep-first); return (kept, dropped)."""
    seen: set[str] = set()
    kept: list[dict] = []
    dropped: list[str] = []
    for p in sorted(points, key=lambda d: d["n"]):
        key = " ".join(str(p["name"]).split()).lower()
        if not key:
            continue
        if key in seen:
            dropped.append(f"#{p['n']} {p['name']}")
            continue
        seen.add(key)
        kept.append(p)
    return kept, dropped


def _grid_points(items: list[tuple[int, str]]) -> list[dict]:
    """Lay names out on a placeholder grid in 10–90% so pins don't stack."""
    n = len(items)
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    out: list[dict] = []
    for i, (num, name) in enumerate(items):
        col, row = i % cols, i // cols
        x = 10.0 + (col * 80.0 / (cols - 1)) if cols > 1 else 50.0
        y = 10.0 + (row * 80.0 / (rows - 1)) if rows > 1 else 50.0
        out.append({"n": num, "name": name, "xPct": round(x, 2), "yPct": round(y, 2)})
    return out


def main() -> None:
    dry = "--dry-run" in sys.argv
    located = [(s, t, a, p) for s, t, a, p in _parse_dreamscape_ts() if p]
    located_slugs = {s for s, _, _, _ in located}

    # Live sheet is authoritative for names-only maps; the bundled fallback fills
    # maps the sheet omits (Dock/Inn/Cafes) and covers offline runs.
    try:
        sheet_maps = _fetch_sheet_maps()
        print(f"fetched {len(sheet_maps)} map(s) from the King Shield sheet")
    except Exception as exc:  # noqa: BLE001 (network/parse — fall back gracefully)
        print(f"warning: sheet fetch failed ({exc}); using bundled fallback only")
        sheet_maps = {}
    names_only = {**_SHEET_FALLBACK, **sheet_maps}

    # (slug, title, points, source, archived)
    plan: list[tuple[str, str, list[dict], str, bool]] = []
    for slug, title, active, pts in located:
        points = [{"n": n, "name": nm, "xPct": x, "yPct": y} for n, nm, x, y in pts]
        plan.append((slug, title, points, "located (dreamscape.ts)", not active))
    for slug, (title, items) in names_only.items():
        if slug in located_slugs:
            continue  # catalog already covers it with real coordinates
        src = "live sheet" if slug in sheet_maps else "bundled fallback"
        # Names-only maps are retired rooms not in the current rotation.
        plan.append(
            (slug, title, _grid_points(items), f"names-only ({src}, placeholder grid)", True)
        )

    total_points = 0
    for slug, title, points, source, archived in plan:
        points, dropped = _dedupe(points)
        total_points += len(points)
        tag = "archived" if archived else "current"
        note = f"  (deduped {len(dropped)}: {', '.join(dropped)})" if dropped else ""
        print(f"{'DRY ' if dry else ''}{slug:14s} {len(points):3d} pts · {tag:8s} · {source}{note}")
        if not dry:
            dreamscape_db.upsert_scene(
                slug,
                title=title,
                source_image="",
                scene_rect=None,  # operator calibrates per scene
                points=points,
                activate=False,  # never steal the active pointer
                archived=archived,
                season=1,  # wostools + King Shield sheet = Season 1
            )

    print(
        f"\n{'(dry run) ' if dry else ''}{len(plan)} scene(s), {total_points} point(s) "
        f"{'would be' if dry else ''} imported."
    )
    if not dry:
        listed = dreamscape_db.list_scenes()
        print(
            f"DB now holds {len(listed['scenes'])} scene(s); active = "
            f"{listed['active'] or '(none)'!r}"
        )


if __name__ == "__main__":
    main()
