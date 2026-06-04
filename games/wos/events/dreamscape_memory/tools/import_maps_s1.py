#!/usr/bin/env python3
"""Import Dreamscape Memory **Season 1** maps (King Shield sheet) into the scene DB.

This is the season-aware successor to the legacy ``import_maps.py`` (which only
pulled names from ``web/lib/dreamscape.ts`` + a bundled fallback and produced
image-less, bare-slug scenes). The King Shield sheet now embeds a *numbered
guide image* per map — exactly like the Season 2/3 sheets — so we mirror
``import_maps_s2.py``: pull the xlsx, extract the left-column guide image for
each map (validated 1:1 by row order), parse the item-name list, OCR the printed
marker numbers to position each pin, and upsert as a ``<slug>-s1`` scene.

The sheet holds **13** left-column guide images: the first 10 are the Season 1
rooms (each carries a ``MAP NAME :`` header + numbered item list in the CSV); the
trailing 3 are the **Multiplayer (Recall Road)** maps (Dock / Inn / Cafes), which
the CSV omits the headers for — their item lists are bundled in ``_MULTIPLAYER``.
Season 1 rooms import as ``<slug>-s1`` (season 1); the Multiplayer rooms import as
``<slug>-mp`` (season ``SEASON_MULTIPLAYER`` = 100).

Images land in ``references/maps/<slug>/<slug>.png`` (one folder per scene).
Stale Season 1 rows (the old
image-less wostools/fallback catalog) are pruned by default so the season reads
as exactly these 10 rooms; pass ``--no-prune`` to keep them.

    uv run python games/wos/events/dreamscape_memory/tools/import_maps_s1.py [--dry-run] [--no-ocr] [--no-prune]
"""
from __future__ import annotations

import csv
import io
import math
import re
import sys
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

import cv2  # type: ignore[import-untyped]
import numpy as np

from config import dreamscape_db
from config.dreamscape_db import SEASON_MULTIPLAYER
from config.paths import repo_root
from services import get_ocr_client

# Multi-pass digit OCR: union over these PSMs × upscales (highest-conf per
# number wins). Dark/busy guides under-detect on any single pass.
_OCR_PSMS = (3, 6, 11, 12)
_OCR_UPSCALES = (2.0, 3.0)
_OCR_MIN_CONF = 0.25

SHEET_ID = "1MzrNWOcg-QpzpdBUulC0iorv2O6dzSrFhBe7nSdjxck"
_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export"
CSV_URL = f"{_BASE}?format=csv&gid=0"
XLSX_URL = f"{_BASE}?format=xlsx"

_MODULE_REL = "games/wos/events/dreamscape_memory"
_MAPS_DIR = repo_root() / _MODULE_REL / "references" / "maps"

# Guide images sit in the left-hand column; the right column holds alt/zoom
# shots we ignore. Anything left of this column index is a canonical guide.
_GUIDE_COL_MAX = 10

# Sheet spells "Hangar" as "Hanger"; keep the codebase slug.
_SLUG_ALIASES = {"hanger": "hangar"}

# Trailing guide images (after the 10 named rooms) are the Multiplayer maps, in
# the sheet's row order. The CSV carries no header/item list for these, so the
# numbered item names are bundled here (from the King Shield community catalog).
# Single-letter / numeric entries are the sheet's watermark glyphs, kept verbatim.
_MULTIPLAYER: list[tuple[str, list[tuple[int, str]]]] = [
    (
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
    (
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
    (
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
]

_MAP_NAME_RE = re.compile(r"MAP NAME\s*:\s*(.+)", re.IGNORECASE)
_ITEM_RE = re.compile(r"(\d+)\.\s*(.+)")
_NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower().replace("'", "")).strip("-")
    return _SLUG_ALIASES.get(s, s) or "scene"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "autopilot-import"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted URL)
        return resp.read()


def _parse_maps(csv_bytes: bytes) -> list[tuple[str, list[tuple[int, str]]]]:
    """Ordered ``[(title, [(n, name), ...]), ...]`` — first name per number wins."""
    maps: list[tuple[str, dict[int, str]]] = []
    cur: dict[int, str] | None = None
    for row in csv.reader(io.StringIO(csv_bytes.decode("utf-8", "replace"))):
        cells = [c.strip() for c in row]
        header = next(
            (m.group(1).strip() for c in cells if (m := _MAP_NAME_RE.match(c))), None
        )
        if header:
            cur = {}
            maps.append((header, cur))
            continue
        if cur is None:
            continue
        for c in cells:
            if im := _ITEM_RE.match(c):
                cur.setdefault(int(im.group(1)), im.group(2).strip())
    return [(t, sorted(d.items())) for t, d in maps]


def _guide_images(xlsx_bytes: bytes) -> list[bytes]:
    """Left-column guide image bytes, ordered top→bottom (one per map)."""
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    rels = {
        r.get("Id"): r.get("Target").split("/")[-1]
        for r in ET.fromstring(z.read("xl/drawings/_rels/drawing1.xml.rels"))
    }
    anchors: list[tuple[int, str]] = []  # (row, media filename)
    for anc in ET.fromstring(z.read("xl/drawings/drawing1.xml")):
        frm = anc.find("xdr:from", _NS)
        blip = anc.find(".//a:blip", _NS)
        if frm is None or blip is None:
            continue
        row = int(frm.find("xdr:row", _NS).text)
        col = int(frm.find("xdr:col", _NS).text)
        if col >= _GUIDE_COL_MAX:
            continue  # right-column alt shot
        anchors.append((row, rels[blip.get(_R_EMBED)]))
    anchors.sort()
    return [_to_png(z.read(f"xl/media/{media}")) for _, media in anchors]


def _to_png(raw: bytes) -> bytes:
    """Re-encode to PNG — the gallery API only serves ``.png`` references."""
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode embedded image")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("could not re-encode image as PNG")
    return buf.tobytes()


def _grid_points(items: list[tuple[int, str]]) -> list[dict]:
    """Placeholder grid in 10–90% so pins don't stack before OCR positioning."""
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


def _ocr_positions(png: bytes, max_n: int) -> dict[int, tuple[float, float]]:
    """OCR the printed marker numbers → ``{n: (xPct, yPct)}`` (best conf wins)."""
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {}
    client = get_ocr_client()
    best: dict[int, tuple[float, float, float]] = {}  # n -> (conf, x, y)
    for psm in _OCR_PSMS:
        for up in _OCR_UPSCALES:
            for m in client.detect_digit_markers(
                img, psm=psm, upscale=up, min_conf=_OCR_MIN_CONF
            ):
                if 1 <= m.value <= max_n and (
                    m.value not in best or m.conf > best[m.value][0]
                ):
                    best[m.value] = (m.conf, m.x_pct, m.y_pct)
    return {n: (round(x, 2), round(y, 2)) for n, (_, x, y) in best.items()}


def _dedupe(points: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept: list[dict] = []
    for p in sorted(points, key=lambda d: d["n"]):
        key = " ".join(str(p["name"]).split()).lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(p)
    return kept


def _import_one(
    title: str,
    items: list[tuple[int, str]],
    img: bytes,
    *,
    slug: str,
    season: int,
    ocr: bool,
    dry: bool,
) -> tuple[str, int, int]:
    """Save the guide image + upsert one scene. Returns (slug, npts, ocr_placed)."""
    # One folder per scene: references/maps/<slug>/<slug>.png.
    rel = f"{_MODULE_REL}/references/maps/{slug}/{slug}.png"
    points = _dedupe(_grid_points(items))
    # Snap pins to the numbers printed on the guide; keep grid for misses.
    pos = _ocr_positions(img, max((p["n"] for p in points), default=0)) if ocr else {}
    for p in points:
        if p["n"] in pos:
            p["xPct"], p["yPct"] = pos[p["n"]]
    if not dry:
        dest = repo_root() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(img)
        dreamscape_db.upsert_scene(
            slug,
            title=title,  # season/category is tracked by the season field, not the title
            source_image=rel,
            scene_rect=None,
            points=points,
            activate=False,
            archived=False,  # current content
            season=season,
        )
    return slug, len(points), len(pos)


def _prune_stale(keep: set[str], dry: bool) -> None:
    """Delete the legacy image-less Season 1 catalog (anything season==1 not kept)."""
    stale = [
        s["slug"]
        for s in dreamscape_db.list_scenes()["scenes"]
        if s["season"] == 1 and s["slug"] not in keep
    ]
    for slug in stale:
        print(f"{'DRY ' if dry else ''}prune stale season-1 {slug!r}")
        if not dry:
            dreamscape_db.delete_scene(slug)
    print(f"{'(dry run) ' if dry else ''}pruned {len(stale)} stale season-1 scene(s)")


def main() -> None:
    dry = "--dry-run" in sys.argv
    ocr = "--no-ocr" not in sys.argv and not dry
    prune = "--no-prune" not in sys.argv
    maps = _parse_maps(_fetch(CSV_URL))
    images = _guide_images(_fetch(XLSX_URL))
    expected = len(maps) + len(_MULTIPLAYER)
    print(
        f"parsed {len(maps)} season-1 map(s) + {len(_MULTIPLAYER)} multiplayer map(s), "
        f"{len(images)} guide image(s)"
    )
    if len(images) != expected:
        sys.exit(
            f"abort: {len(images)} guide images != {expected} expected "
            f"({len(maps)} season-1 + {len(_MULTIPLAYER)} multiplayer) — the sheet "
            "layout changed; re-check the column/row pairing before importing."
        )

    if not dry:
        _MAPS_DIR.mkdir(parents=True, exist_ok=True)

    # First N images align 1:1 (row order) with the CSV-headed Season 1 rooms; the
    # trailing 3 are the Multiplayer maps (Dock/Inn/Cafes), names bundled above.
    season1_imgs = images[: len(maps)]
    mp_imgs = images[len(maps) :]

    keep: set[str] = set()
    total = placed = 0
    for (title, items), img in zip(maps, season1_imgs):
        slug = f"{_slugify(title)}-s1"
        keep.add(slug)
        slug, npts, n_ocr = _import_one(
            title, items, img, slug=slug, season=1, ocr=ocr, dry=dry
        )
        total += npts
        placed += n_ocr
        ocr_note = f" · OCR placed {n_ocr}/{npts}" if ocr else ""
        print(f"{'DRY ' if dry else ''}{slug:18s} S1  {npts:3d} pts{ocr_note}")

    for (title, items), img in zip(_MULTIPLAYER, mp_imgs):
        slug = f"{_slugify(title)}-mp"
        slug, npts, n_ocr = _import_one(
            title, items, img, slug=slug, season=SEASON_MULTIPLAYER, ocr=ocr, dry=dry
        )
        total += npts
        placed += n_ocr
        ocr_note = f" · OCR placed {n_ocr}/{npts}" if ocr else ""
        print(f"{'DRY ' if dry else ''}{slug:18s} MP  {npts:3d} pts{ocr_note}")

    if prune:
        _prune_stale(keep, dry)

    placed_note = f", {placed} OCR-placed" if ocr else ""
    print(
        f"\n{'(dry run) ' if dry else ''}{len(maps)} season-1 + {len(_MULTIPLAYER)} "
        f"multiplayer scene(s), {total} point(s){placed_note}."
    )
    if not dry:
        listed = dreamscape_db.list_scenes()
        print(f"DB now holds {len(listed['scenes'])} scene(s); active = {listed['active']!r}")


if __name__ == "__main__":
    main()
