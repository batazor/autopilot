#!/usr/bin/env python3
"""Fetch the Dreamscape Memory scene catalog + item-location guide images.

Source: https://wostools.net/wiki/events/dreamscape-memory (a Next.js app that
embeds the scene registry in a client JS chunk). This scrapes that registry,
downloads every scene image into ``web/public/dreamscape/``, and regenerates
``web/lib/dreamscape.ts``.

Run from the repo root (or anywhere):

    uv run python web/scripts/fetch_dreamscape.py

Re-run when the event rotation changes (new scenes / extra variant images).
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

WIKI_URL = "https://wostools.net/wiki/events/dreamscape-memory"
BASE = "https://wostools.net"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

WEB_DIR = Path(__file__).resolve().parents[1]
IMG_DIR = WEB_DIR / "public" / "dreamscape"
DATA_FILE = WEB_DIR / "lib" / "dreamscape.ts"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read()


def _find_scene_chunk(html: str) -> str:
    """Return the JS chunk text that contains the scene registry."""
    chunks = sorted(set(re.findall(r"/_next/static/chunks/[\w./-]+\.js", html)))
    for c in chunks:
        text = _get(BASE + c).decode("utf-8", "ignore")
        if "ballroom" in text and "extraSrcs" in text:
            return text
    raise SystemExit("scene registry chunk not found — page structure changed")


def _parse_scenes(js: str) -> list[dict]:
    scenes: list[dict] = []
    for m in re.finditer(r'\{slug:"', js):
        i = m.start()
        depth = 0
        j = i
        while j < len(js):
            if js[j] == "{":
                depth += 1
            elif js[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        obj = js[i : j + 1]
        slug = re.search(r'slug:"([\w-]+)"', obj).group(1)
        src = re.search(r'src:"([^"]+)"', obj).group(1)
        width = int(re.search(r"width:(\d+)", obj).group(1))
        height = int(re.search(r"height:(\d+)", obj).group(1))
        em = re.search(r"extraSrcs:\[([^\]]*)\]", obj)
        extras = (
            re.findall(r"/images/dreamscape/[\w-]+\.webp", em.group(1)) if em else []
        )
        rm = re.search(
            r"sceneRect:\{left:([\d.]+),top:([\d.]+),right:([\d.]+),bottom:([\d.]+)\}",
            obj,
        )
        rect = (
            {k: float(v) for k, v in zip("left top right bottom".split(), rm.groups())}
            if rm
            else None
        )
        scenes.append(
            {
                "slug": slug,
                "src": src,
                "width": width,
                "height": height,
                "extraSrcs": extras,
                "sceneRect": rect,
            }
        )
    # de-dupe by slug, preserve first occurrence order
    seen: dict[str, dict] = {}
    for s in scenes:
        seen.setdefault(s["slug"], s)
    return list(seen.values())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_array(text: str, start: int) -> int:
    """Return index of the ``]`` that closes the ``[`` at ``start``."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _parse_markers(js: str) -> dict[str, list[dict]]:
    """``MapName:[{n:1,x:..,y:..,stage:..,tentative:!0}]`` → normalized name → list."""
    markers: dict[str, list[dict]] = {}
    for m in re.finditer(r"([A-Za-z]+):\[\{n:1,x:", js):
        name = m.group(1)
        lb = js.index("[", m.start())
        arr = js[lb : _match_array(js, lb) + 1]
        objs: list[dict] = []
        for om in re.finditer(r"\{n:(\d+),x:([-\d.]+),y:([-\d.]+)([^}]*)\}", arr):
            rest = om.group(4)
            stage = re.search(r"stage:(\d+)", rest)
            objs.append(
                {
                    "n": int(om.group(1)),
                    "x": float(om.group(2)),
                    "y": float(om.group(3)),
                    "stage": int(stage.group(1)) if stage else None,
                    "tentative": "tentative:!0" in rest,
                }
            )
        markers[_norm(name)] = objs
    return markers


def _parse_item_names(html: str) -> dict[str, list[str]]:
    """``"mapName":"X","items":[...]`` (RSC, escaped) → normalized name → names."""
    items: dict[str, list[str]] = {}
    for m in re.finditer(r'mapName\\":\\"([^"\\]+)\\",\\"items\\":\[', html):
        start = html.index("[", m.end() - 1)
        depth = 0
        i = start
        while i < len(html):
            if html[i] == "[":
                depth += 1
            elif html[i] == "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        names = re.findall(r'\\"([^"\\]+)\\"', html[start : i + 1])
        items[_norm(m.group(1))] = names
    return items


def _attach_points(scenes: list[dict], js: str, html: str) -> None:
    """Compute final image-% coordinates per item and attach as ``scene['points']``.

    The site renders each marker at ``left = (rect.left + x*(rect.right-rect.left))``
    and ``top = (rect.top + y*(rect.bottom-rect.top))`` of the displayed image.
    Item name is ``items[n-1]`` for that map.
    """
    markers = _parse_markers(js)
    names_by_map = _parse_item_names(html)
    for s in scenes:
        key = _norm(s["slug"])
        rect = s["sceneRect"] or {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}
        fx = rect["right"] - rect["left"]
        fy = rect["bottom"] - rect["top"]
        names = names_by_map.get(key, [])
        pts = []
        for e in markers.get(key, []):
            name = names[e["n"] - 1] if e["n"] - 1 < len(names) else f"Item #{e['n']}"
            pts.append(
                {
                    "n": e["n"],
                    "name": name,
                    "xPct": round((rect["left"] + e["x"] * fx) * 100, 2),
                    "yPct": round((rect["top"] + e["y"] * fy) * 100, 2),
                    "stage": e["stage"],
                    "tentative": e["tentative"],
                }
            )
        s["points"] = pts


def _download_images(scenes: list[dict]) -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    urls = sorted({s["src"] for s in scenes} | {e for s in scenes for e in s["extraSrcs"]})
    for u in urls:
        dest = IMG_DIR / Path(u).name
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        dest.write_bytes(_get(BASE + u))
    print(f"images: {len(urls)} in {IMG_DIR}")


def _local(u: str) -> str:
    return "/dreamscape/" + Path(u).name


def _write_data(scenes: list[dict]) -> None:
    def title(slug: str) -> str:
        return slug.replace("-", " ").title()

    out: list[str] = [
        "// AUTO-GENERATED by web/scripts/fetch_dreamscape.py — do not edit by hand.",
        "// Dreamscape Memory scene catalog: base screenshot + item-location guides.",
        "",
        "export type DreamscapeRect = {",
        "  left: number;",
        "  top: number;",
        "  right: number;",
        "  bottom: number;",
        "};",
        "",
        "/** One findable item: name + position as a percentage of the scene image. */",
        "export type DreamscapePoint = {",
        "  /** Marker number on the source guide. */",
        "  n: number;",
        "  name: string;",
        "  /** Left position, percent of image width (0-100). */",
        "  xPct: number;",
        "  /** Top position, percent of image height (0-100). */",
        "  yPct: number;",
        "  /** Stage the item first appears in, when known. */",
        "  stage: number | null;",
        "  /** Community-flagged as an unconfirmed location. */",
        "  tentative: boolean;",
        "};",
        "",
        "export type DreamscapeScene = {",
        "  slug: string;",
        "  title: string;",
        "  /** Base scene screenshot (clean). */",
        "  src: string;",
        "  width: number;",
        "  height: number;",
        "  /** Item-location guide images (markers drawn on the scene). */",
        "  images: string[];",
        "  /** Normalized playable-area rectangle within the image, when known. */",
        "  sceneRect: DreamscapeRect | null;",
        "  /** Findable items with positions (% of the base image). */",
        "  points: DreamscapePoint[];",
        "  /** Active = current event rotation; archived scenes stay 1:1 reusable. */",
        "  active: boolean;",
        "};",
        "",
        "export const DREAMSCAPE_SCENES: DreamscapeScene[] = [",
    ]
    for s in scenes:
        active = s["sceneRect"] is None
        imgs = [_local(s["src"])] + [_local(e) for e in s["extraSrcs"]]
        if s["sceneRect"]:
            r = s["sceneRect"]
            rect = (
                f"{{ left: {r['left']}, top: {r['top']}, "
                f"right: {r['right']}, bottom: {r['bottom']} }}"
            )
        else:
            rect = "null"
        points = ", ".join(
            "{ n: %d, name: %s, xPct: %s, yPct: %s, stage: %s, tentative: %s }"
            % (
                p["n"],
                json.dumps(p["name"]),
                p["xPct"],
                p["yPct"],
                "null" if p["stage"] is None else p["stage"],
                "true" if p["tentative"] else "false",
            )
            for p in s.get("points", [])
        )
        out += [
            "  {",
            f"    slug: {json.dumps(s['slug'])},",
            f"    title: {json.dumps(title(s['slug']))},",
            f"    src: {json.dumps(_local(s['src']))},",
            f"    width: {s['width']},",
            f"    height: {s['height']},",
            f"    images: {json.dumps(imgs)},",
            f"    sceneRect: {rect},",
            f"    points: [{points}],",
            f"    active: {'true' if active else 'false'},",
            "  },",
        ]
    out += [
        "];",
        "",
        "export const DREAMSCAPE_ACTIVE = DREAMSCAPE_SCENES.filter((s) => s.active);",
        "export const DREAMSCAPE_ARCHIVE = DREAMSCAPE_SCENES.filter((s) => !s.active);",
        "",
        "export function dreamscapeScene(slug: string): DreamscapeScene | undefined {",
        "  return DREAMSCAPE_SCENES.find((s) => s.slug === slug);",
        "}",
        "",
    ]
    DATA_FILE.write_text("\n".join(out))
    print(f"data: {len(scenes)} scenes -> {DATA_FILE}")


def main() -> None:
    html = _get(WIKI_URL).decode("utf-8", "ignore")
    js = _find_scene_chunk(html)
    scenes = _parse_scenes(js)
    _attach_points(scenes, js, html)
    _download_images(scenes)
    _write_data(scenes)
    pts = sum(len(s["points"]) for s in scenes)
    print(f"points: {pts} across {sum(1 for s in scenes if s['points'])} scenes")


if __name__ == "__main__":
    main()
