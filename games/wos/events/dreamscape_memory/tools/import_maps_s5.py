#!/usr/bin/env python3
"""Import Dreamscape Memory **Season 5** scenes (wostools catalog) into the scene DB.

The catalog moved since ``web/scripts/fetch_dreamscape.py`` was written: the
scene registry now lives inline in a client chunk of
``https://wostools.net/games/dreamscape-memory`` as one object per scene —
``{name, era, season, multiplayer, items:[...], image:{slug,src,width,height,
extraSrcs}, coords:[{n,x,y,w,h,rot,stage}]}``. Item name for marker ``n`` is
``items[n-1]``; the site renders each marker centered at
``left = rect.left + x*(rect.right-rect.left)`` of the image (rect defaults to
the identity for Season 5, whose images are already cropped to the scene panel).

Scenes land as ``<room>-s5`` (``-s5-mp`` for the co-op Recall Road maps, which
go in the Multiplayer guides bucket) so they never clobber the Season 1-3 rooms
that share a name. The scene image is re-encoded to PNG under
``references/maps/<slug>/``. The catalog also carries a per-stage shot of each
room; those go in the scene's gallery too (``--no-extras`` keeps the import to
one image per scene — they cost ~55 MB of PNG across the season and only the
operator gallery shows them; the solver reads points, not pixels).

``scene_rect`` maps guide-image % to game-frame %. The Season 5 crops are the
in-game scene panel: measured on ``references/practice_level.png`` the panel's
outer border is (56, 74, 608, 989) px of a 720x1280 frame — aspect 0.6148,
matching the 523x852 catalog crops (0.6138), so ``_PANEL_RECT`` is applied
directly. Crops whose aspect is off by more than ``_ASPECT_TOL`` are a different
framing (the wide co-op Dock); they import with no rect and are reported so an
operator can set one in the onboarding editor before the solver taps them.

    uv run python games/wos/events/dreamscape_memory/tools/import_maps_s5.py [--dry-run] [--no-extras]
"""

from __future__ import annotations

import re
import sys
import urllib.request

import cv2  # type: ignore[import-untyped]
import numpy as np

from config import dreamscape_db
from config.paths import repo_root

BASE = "https://wostools.net"
PAGE_URL = f"{BASE}/games/dreamscape-memory"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

SEASON = 5

_MODULE_REL = "games/wos/events/dreamscape_memory"
_MAPS_DIR = repo_root() / _MODULE_REL / "references" / "maps"

# Scene panel inside the 720x1280 game frame, as % — see the module docstring.
_PANEL_RECT = {"left": 7.78, "top": 5.78, "width": 84.44, "height": 77.27}
_PANEL_ASPECT = 608 / 989
_ASPECT_TOL = 0.05  # relative; beyond this the crop isn't the plain panel

# The catalog seeds each item list with a watermark string to catch scrapers
# ("wos.tools.net is much better than WSCO (garden-s5)", "wostoo.lsnet is the
# original source …"). It occupies a real index (names are ``items[n-1]``), so
# it is skipped as a *point* while the surrounding names keep their numbering.
# The site scrambles the punctuation between runs, so match on letters only.
_CANARY_RE = re.compile(r"wostools|wsco|canary")


def _is_canary(name: str) -> bool:
    return bool(_CANARY_RE.search(re.sub(r"[^a-z]", "", name.lower())))


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _match_brace(text: str, start: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``start`` (-1 if unbalanced)."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _registry_chunk(html: str) -> str:
    """The client chunk holding the scene registry."""
    for path in sorted(set(re.findall(r"/_next/static/chunks/[\w./-]+\.js", html))):
        text = _get(BASE + path).decode("utf-8", "ignore")
        if "extraSrcs" in text and "season:" in text and "coords:" in text:
            return text
    msg = "scene registry chunk not found — the site's page structure changed"
    raise SystemExit(msg)


def _parse_scenes(js: str, season: int) -> list[dict]:
    """Registry objects for ``season`` that carry an image and coordinates."""
    scenes: list[dict] = []
    for m in re.finditer(r'\{name:"', js):
        end = _match_brace(js, m.start())
        if end < 0:
            continue
        obj = js[m.start() : end + 1]
        if f"season:{season}," not in obj:
            continue
        image = re.search(r'image:\{slug:"([\w-]+)",src:"([^"]+)",width:(\d+),height:(\d+)', obj)
        if image is None:
            continue  # catalog entry with no guide image yet
        items_m = re.search(r"items:\[(.*?)\],(?:image|coords|fallback)", obj, re.DOTALL)
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', items_m.group(1)) if items_m else []
        extras_m = re.search(r"extraSrcs:\[([^\]]*)\]", obj)
        extras = re.findall(r'"([^"]+)"', extras_m.group(1)) if extras_m else []
        rect_m = re.search(
            r"sceneRect:\{left:([\d.]+),top:([\d.]+),right:([\d.]+),bottom:([\d.]+)\}",
            obj,
        )
        rect = (
            dict(
                zip(
                    ("left", "top", "right", "bottom"),
                    (float(v) for v in rect_m.groups()),
                    strict=True,
                )
            )
            if rect_m
            else {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}
        )
        coords = [
            {"n": int(c[0]), "x": float(c[1]), "y": float(c[2])}
            for c in re.findall(r"\{n:(\d+),x:([-\d.]+),y:([-\d.]+)", obj)
        ]
        if not coords:
            continue
        scenes.append(
            {
                "name": re.search(r'^\{name:"([^"]+)"', obj).group(1),
                "multiplayer": "multiplayer:!0" in obj,
                "items": items,
                "slug": image.group(1),
                "src": image.group(2),
                "width": int(image.group(3)),
                "height": int(image.group(4)),
                "extras": extras,
                "rect": rect,
                "coords": coords,
            }
        )
    return scenes


def _points(scene: dict) -> list[dict]:
    """``coords`` -> ``[{n,name,xPct,yPct}]``: named, watermark-free, deduped.

    The solver keys taps by item name and can't hold two positions under one
    word, so within-scene duplicates collapse to the lowest marker number.
    """
    rect = scene["rect"]
    fx = rect["right"] - rect["left"]
    fy = rect["bottom"] - rect["top"]
    items = scene["items"]
    out: list[dict] = []
    seen: set[str] = set()
    for c in sorted(scene["coords"], key=lambda c: c["n"]):
        name = items[c["n"] - 1] if c["n"] - 1 < len(items) else ""
        name = " ".join(str(name).split())
        if not name or _is_canary(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "n": c["n"],
                "name": name,
                "xPct": round((rect["left"] + c["x"] * fx) * 100, 2),
                "yPct": round((rect["top"] + c["y"] * fy) * 100, 2),
            }
        )
    return out


def _to_png(raw: bytes) -> bytes:
    """Re-encode to PNG — the gallery API only serves ``.png`` references."""
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        msg = "could not decode catalog image"
        raise ValueError(msg)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        msg = "could not re-encode image as PNG"
        raise ValueError(msg)
    return buf.tobytes()


def _target_slug(scene: dict) -> str:
    base = re.sub(r"-s5$", "", scene["slug"])
    return f"{base}-s5-mp" if scene["multiplayer"] else f"{base}-s5"


def _title(name: str) -> str:
    """``"Garden (Season 5)"`` -> ``"Garden"`` (the on-screen level name)."""
    return re.sub(r"\s*\(Season \d+\)\s*$", "", name).strip()


def _rect_for(scene: dict) -> dict | None:
    aspect = scene["width"] / scene["height"]
    off = abs(aspect - _PANEL_ASPECT) / _PANEL_ASPECT
    return dict(_PANEL_RECT) if off <= _ASPECT_TOL else None


def main() -> None:
    dry = "--dry-run" in sys.argv
    extras = "--no-extras" not in sys.argv
    scenes = _parse_scenes(_registry_chunk(_get(PAGE_URL).decode("utf-8", "ignore")), SEASON)
    if not scenes:
        sys.exit(f"abort: no Season {SEASON} scenes with coordinates in the catalog")
    print(f"parsed {len(scenes)} Season {SEASON} scene(s)")

    total = 0
    needs_rect: list[str] = []
    for scene in scenes:
        slug = _target_slug(scene)
        points = _points(scene)
        rect = _rect_for(scene)
        total += len(points)
        if rect is None:
            needs_rect.append(slug)
        srcs = [scene["src"], *(scene["extras"] if extras else [])]
        rels = [
            f"{_MODULE_REL}/references/maps/{slug}/{slug}{'' if i == 0 else f'-{i + 1}'}.png" for i in range(len(srcs))
        ]
        note = "" if rect else "  ← no rect (crop framing differs)"
        print(
            f"{'DRY ' if dry else ''}{slug:18s} {len(points):3d} pts · "
            f"{len(srcs):2d} image(s) · {scene['width']}x{scene['height']}{note}"
        )
        if dry:
            continue
        for src, rel in zip(srcs, rels, strict=True):
            dest = repo_root() / rel
            if dest.exists() and dest.stat().st_size > 1000:
                continue  # already pulled — re-runs only refresh the DB rows
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_to_png(_get(BASE + src)))
        dreamscape_db.upsert_scene(
            slug,
            title=_title(scene["name"]),
            source_image=rels[0],
            scene_rect=rect,
            points=points,
            activate=False,
            archived=False,  # Season 5 is the current rotation
            season=(dreamscape_db.SEASON_MULTIPLAYER if scene["multiplayer"] else SEASON),
            images=rels,
        )

    print(f"\n{'(dry run) ' if dry else ''}{len(scenes)} scene(s), {total} point(s).")
    if needs_rect:
        print("set a scene_rect in the onboarding editor before solving: " + ", ".join(needs_rect))
    if not dry:
        listed = dreamscape_db.list_scenes()
        print(f"DB now holds {len(listed['scenes'])} scene(s); active = {listed['active']!r}")


if __name__ == "__main__":
    main()
