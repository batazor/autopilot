#!/usr/bin/env python3
"""Import Dreamscape Memory **Season 2** maps (Shield WOS sheet) into the scene DB.

Unlike the Season 1 sheet (names only), this sheet embeds a *numbered guide
image* per map. We pull the xlsx export, extract the left-column guide image for
each map (validated 1:1 by row order), parse the item-name list, and upsert each
as a ``<slug>-s2`` scene — the suffix avoids clobbering Season 1 scenes that
share a room name (court/arena/farmhouse).

Images land in ``references/maps/<slug>-s2/<slug>-s2.png`` (one folder per
scene). The sheet has no coordinates,
but the guide image has the numbers printed on it, so by default we OCR them and
snap each pin to its real position (multi-pass digit OCR); unread numbers fall
back to a placeholder grid for manual dragging. Pass ``--no-ocr`` to skip the
(slow) OCR pass and keep every pin on the grid.

    uv run python games/wos/events/dreamscape_memory/tools/import_maps_s2.py [--dry-run] [--no-ocr]
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
from config.paths import repo_root
from services import get_ocr_client

# Multi-pass digit OCR: union over these PSMs × upscales (highest-conf per
# number wins). Dark/busy guides under-detect on any single pass.
_OCR_PSMS = (3, 6, 11, 12)
_OCR_UPSCALES = (2.0, 3.0)
_OCR_MIN_CONF = 0.25

SHEET_ID = "10n1P9l1b9G7vG-HbkG6Br2BdETIT2vwPzUi-AyqO4cQ"
_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export"
CSV_URL = f"{_BASE}?format=csv&gid=0"
XLSX_URL = f"{_BASE}?format=xlsx"

_MODULE_REL = "games/wos/events/dreamscape_memory"
_MAPS_DIR = repo_root() / _MODULE_REL / "references" / "maps"

# Guide images sit in the left-hand column; the right column holds alt/zoom
# shots we ignore. Anything left of this column index is a canonical guide.
_GUIDE_COL_MAX = 10

_MAP_NAME_RE = re.compile(r"MAP NAME\s*:\s*(.+)", re.IGNORECASE)
_ITEM_RE = re.compile(r"(\d+)\.\s*(.+)")
_NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.strip().lower().replace("'", "")).strip("-")


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "autopilot-import"})
    with urllib.request.urlopen(req, timeout=60) as resp:
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
        msg = "could not decode embedded image"
        raise ValueError(msg)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        msg = "could not re-encode image as PNG"
        raise ValueError(msg)
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


def main() -> None:
    dry = "--dry-run" in sys.argv
    ocr = "--no-ocr" not in sys.argv and not dry
    maps = _parse_maps(_fetch(CSV_URL))
    images = _guide_images(_fetch(XLSX_URL))
    print(f"parsed {len(maps)} map(s), {len(images)} guide image(s)")
    if len(images) != len(maps):
        sys.exit(
            f"abort: {len(images)} guide images != {len(maps)} maps — the sheet "
            "layout changed; re-check the column/row pairing before importing."
        )

    if not dry:
        _MAPS_DIR.mkdir(parents=True, exist_ok=True)

    total = placed = 0
    for (title, items), img in zip(maps, images, strict=False):
        slug = f"{_slugify(title)}-s2"
        # One folder per scene: references/maps/<slug>/<slug>.png.
        rel = f"{_MODULE_REL}/references/maps/{slug}/{slug}.png"
        points = _dedupe(_grid_points(items))
        total += len(points)
        # Snap pins to the numbers printed on the guide; keep grid for misses.
        pos = _ocr_positions(img, max((p["n"] for p in points), default=0)) if ocr else {}
        for p in points:
            if p["n"] in pos:
                p["xPct"], p["yPct"] = pos[p["n"]]
                placed += 1
        ocr_note = f" · OCR placed {len(pos)}/{len(points)}" if ocr else ""
        print(f"{'DRY ' if dry else ''}{slug:22s} {len(points):3d} pts · {len(img):>7d}B{ocr_note}")
        if not dry:
            dest = repo_root() / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(img)
            dreamscape_db.upsert_scene(
                slug,
                title=title,  # season is tracked by the season field, not the title
                source_image=rel,
                scene_rect=None,
                points=points,
                activate=False,
                archived=False,  # Season 2 is current content
                season=2,
            )
            # This season-specific scene supersedes the generic wostools entry
            # for the same room (if the Season 1 catalog imported one).
            base = _slugify(title)
            if dreamscape_db.delete_scene(base):
                print(f"  removed wostools dup {base!r} (superseded by {slug})")

    placed_note = f", {placed} OCR-placed" if ocr else ""
    print(f"\n{'(dry run) ' if dry else ''}{len(maps)} scene(s), {total} point(s){placed_note}.")
    if not dry:
        listed = dreamscape_db.list_scenes()
        print(f"DB now holds {len(listed['scenes'])} scene(s); active = {listed['active']!r}")


if __name__ == "__main__":
    main()
