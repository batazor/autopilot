"""Backend for the Dreamscape Memory map-onboarding workflow.

Operators build the solver's scene maps here: upload a numbered guide image,
OCR the digit markers to get positions, paste the community item-name list,
calibrate where the scene sits in the game frame, and save it into the module's
scene database (:mod:`config.dreamscape_db`, the solver source of truth).

Pure helpers (``slugify``, ``parse_name_list``, ``join_markers_to_names``) are
unit-tested; storage is delegated to ``dreamscape_db`` and guide images are
written under the module references. The sync OCR call is offloaded to a worker
thread by the router.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any, TypedDict

import cv2  # type: ignore[import-untyped]
import numpy as np

from config import dreamscape_db
from config.paths import repo_root
from ocr.word_cleaning import normalize_word_text
from services import get_ocr_client

logger = logging.getLogger(__name__)

_MODULE_REL = "games/wos/events/dreamscape_memory"
_MODULE_DIR = repo_root() / _MODULE_REL
_MAPS_IMG_DIR = _MODULE_DIR / "references" / "maps"


# ── Result shapes ─────────────────────────────────────────────────────────────


class MarkerDTO(TypedDict):
    value: int
    xPct: float
    yPct: float
    conf: float


class DetectMarkersResult(TypedDict):
    width: int
    height: int
    psm: int
    markers: list[MarkerDTO]
    expected: int | None
    missing: list[int]


class ParsedNameItem(TypedDict):
    n: int
    name: str


class ParseNamesResult(TypedDict):
    items: list[ParsedNameItem]
    warnings: list[str]


class ScenePoint(TypedDict):
    n: int
    name: str
    xPct: float
    yPct: float


class SceneRect(TypedDict):
    left: float
    top: float
    width: float
    height: float


class SceneSummary(TypedDict):
    slug: str
    title: str
    alt_title: str
    alt_titles: list[str]
    source_image: str
    point_count: int
    active: bool
    archived: bool
    season: int


class ListMapsResult(TypedDict):
    active: str
    scenes: list[SceneSummary]


class SceneDetail(TypedDict):
    slug: str
    title: str
    alt_title: str
    alt_titles: list[str]
    source_image: str
    images: list[str]
    scene_rect: SceneRect | None
    points: list[ScenePoint]
    active: bool
    archived: bool
    season: int


class SaveMapResult(TypedDict):
    ok: bool
    slug: str
    point_count: int
    active: str


# ── Pure helpers (unit-tested) ────────────────────────────────────────────────


def slugify(title: str) -> str:
    """Filesystem/URL-safe scene slug: lowercase ``[a-z0-9-]`` only."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug or "scene"


def _clean_slug(slug: str) -> str:
    """Normalize a path/slug param to a safe slug (strips traversal/odd chars)."""
    if not slug.strip():
        msg = "empty scene slug"
        raise ValueError(msg)
    return slugify(slug)


_NAME_LINE = re.compile(r"^\s*(\d+)\s*[.):\-\t ]+\s*(.+?)\s*$")


def parse_name_list(text: str) -> ParseNamesResult:
    """Parse a pasted ``"1. Parachutte"`` / ``"2 Envelope"`` list into items.

    Tolerant of ``N.`` / ``N)`` / ``N -`` / ``N:`` / tab separators. Warns on
    duplicate numbers, gaps in ``1..max``, duplicate names, and ignored lines.
    """
    items: list[ParsedNameItem] = []
    warnings: list[str] = []
    seen_n: dict[int, str] = {}
    seen_name: dict[str, int] = {}

    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = _NAME_LINE.match(raw)
        if not m:
            warnings.append(f"ignored line (no leading number): {raw.strip()!r}")
            continue
        n = int(m.group(1))
        name = " ".join(m.group(2).split())
        if n in seen_n:
            warnings.append(f"duplicate number {n} ({seen_n[n]!r} vs {name!r})")
            continue
        norm = name.lower()
        if norm in seen_name:
            warnings.append(f"duplicate name {name!r} (#{seen_name[norm]} and #{n})")
        seen_n[n] = name
        seen_name[norm] = n
        items.append({"n": n, "name": name})

    items.sort(key=lambda it: it["n"])
    if items:
        present = {it["n"] for it in items}
        gaps = [i for i in range(1, max(present) + 1) if i not in present]
        if gaps:
            warnings.append(f"missing numbers: {gaps}")
    return {"items": items, "warnings": warnings}


def join_markers_to_names(
    markers: list[MarkerDTO],
    names: list[ParsedNameItem],
) -> tuple[list[ScenePoint], list[int], list[int]]:
    """Join detected markers to names on ``n``.

    Returns ``(points, unmatched_numbers, unmatched_names)`` where
    ``unmatched_numbers`` are detected marker values with no name and
    ``unmatched_names`` are item numbers with no detected marker.
    """
    name_by_n = {it["n"]: it["name"] for it in names}
    marker_by_value = {m["value"]: m for m in markers}
    points: list[ScenePoint] = []
    unmatched_numbers: list[int] = []
    for m in markers:
        name = name_by_n.get(m["value"])
        if name is None:
            unmatched_numbers.append(m["value"])
            continue
        points.append(
            {"n": m["value"], "name": name, "xPct": m["xPct"], "yPct": m["yPct"]}
        )
    unmatched_names = sorted(n for n in name_by_n if n not in marker_by_value)
    points.sort(key=lambda p: p["n"])
    return points, sorted(unmatched_numbers), unmatched_names


# ── Image decode / IO ─────────────────────────────────────────────────────────


def _decode_image(image_bytes: bytes) -> np.ndarray | None:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None else None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as f:
        f.write(data)
        tmp = f.name
    Path(tmp).replace(path)


# ── Detection ─────────────────────────────────────────────────────────────────


def detect_markers_on_image(
    image_bytes: bytes,
    *,
    expected: int | None = None,
    psm: int | None = None,
) -> DetectMarkersResult:
    """OCR digit markers on an uploaded guide image (best-effort, sync)."""
    img = _decode_image(image_bytes)
    if img is None:
        msg = "could not decode image"
        raise ValueError(msg)
    height, width = int(img.shape[0]), int(img.shape[1])
    client = get_ocr_client()

    if psm is not None:
        used_psm = psm
        markers = client.detect_digit_markers(img, psm=psm)
    else:
        used_psm = 11
        markers = client.detect_digit_markers(img, psm=11)
        # Sparse-text PSM 11 occasionally under-detects on busy art; retry with
        # PSM 12 (sparse + OSD) and keep whichever found more markers.
        if expected is None or len(markers) < expected:
            alt = client.detect_digit_markers(img, psm=12)
            if len(alt) > len(markers):
                markers, used_psm = alt, 12

    dtos: list[MarkerDTO] = [
        {"value": m.value, "xPct": m.x_pct, "yPct": m.y_pct, "conf": m.conf}
        for m in markers
    ]
    found = {m["value"] for m in dtos}
    missing = (
        [i for i in range(1, expected + 1) if i not in found] if expected else []
    )
    return {
        "width": width,
        "height": height,
        "psm": used_psm,
        "markers": dtos,
        "expected": expected,
        "missing": missing,
    }


# ── Scene image + map persistence ─────────────────────────────────────────────


def save_scene_image(slug: str, image_bytes: bytes) -> dict[str, Any]:
    """Persist a guide image into the module's reference collection (as PNG)."""
    slug = _clean_slug(slug)
    img = _decode_image(image_bytes)
    if img is None:
        msg = "could not decode image"
        raise ValueError(msg)
    ok, buf = cv2.imencode(".png", img)
    if not ok or buf is None:
        msg = "could not re-encode image as PNG"
        raise ValueError(msg)
    # One folder per scene: references/maps/<slug>/<slug>.png.
    _atomic_write_bytes(_MAPS_IMG_DIR / slug / f"{slug}.png", buf.tobytes())
    return {
        "ok": True,
        "source_image": f"{_MODULE_REL}/references/maps/{slug}/{slug}.png",
    }


def _coerce_rect(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        return {
            "left": float(raw["left"]),
            "top": float(raw["top"]),
            "width": float(raw["width"]),
            "height": float(raw["height"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"invalid scene_rect: {raw!r}"
        raise ValueError(msg) from exc


def save_scene(
    slug: str,
    *,
    title: str,
    source_image: str,
    scene_rect: Any,
    points: list[dict[str, Any]],
    activate: bool,
    alt_title: str | None = None,
    alt_titles: list[str] | None = None,
) -> SaveMapResult:
    """Upsert a scene into the module DB (optionally activating it)."""
    slug = _clean_slug(slug)
    rect = _coerce_rect(scene_rect)

    points_out: list[ScenePoint] = []
    seen_names: set[str] = set()
    collisions: list[str] = []
    for p in points:
        name = " ".join(str(p.get("name") or "").split())
        if not name:
            continue
        try:
            x = round(float(p["xPct"]), 3)
            y = round(float(p["yPct"]), 3)
            n = int(p["n"])
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"invalid point: {p!r}"
            raise ValueError(msg) from exc
        if name in seen_names:
            collisions.append(name)
            continue
        seen_names.add(name)
        points_out.append({"n": n, "name": name, "xPct": x, "yPct": y})
    if collisions:
        msg = f"duplicate item name(s) in scene {slug!r}: {sorted(set(collisions))}"
        raise ValueError(msg)

    points_out.sort(key=lambda pt: pt["n"])
    result = dreamscape_db.upsert_scene(
        slug,
        title=title.strip() or slug,
        source_image=source_image.replace("\\", "/").strip().lstrip("/"),
        scene_rect=rect,
        points=points_out,
        activate=activate,
        alt_title=alt_title.strip() if alt_title is not None else None,
        alt_titles=alt_titles,
    )
    return {
        "ok": True,
        "slug": slug,
        "point_count": int(result["point_count"]),
        "active": str(result["active"]),
    }


class DetectSceneResult(TypedDict):
    # The detected scene slug, or "" when the words match nothing.
    slug: str
    title: str
    # How many of the supplied words landed in the matched scene (0 when none).
    matched: int


def detect_scene(words: list[str]) -> DetectSceneResult:
    """Auto-detect the scene from the on-screen item words (3→2→1 overlap).

    Returns the matched scene (or an empty slug when nothing matches) so the live
    editor can show the recognised scene without OCR-ing an unreliable title.
    """
    scene = dreamscape_db.detect_scene_by_words([str(w) for w in words])
    if scene is None:
        return {"slug": "", "title": "", "matched": 0}
    names = {
        normalize_word_text(str(p.get("name", "")))
        for p in (scene.get("points") or [])
        if isinstance(p, dict)
    }
    matched = sum(1 for w in words if normalize_word_text(str(w)) in names)
    return {
        "slug": str(scene.get("slug") or ""),
        "title": str(scene.get("title") or ""),
        "matched": matched,
    }


def activate_scene(slug: str) -> SaveMapResult:
    """Make ``slug`` the active scene (the one the solver taps)."""
    slug = _clean_slug(slug)
    if not dreamscape_db.set_active(slug):
        msg = f"unknown scene: {slug}"
        raise FileNotFoundError(msg)
    scene = dreamscape_db.get_scene(slug)
    point_count = len(scene.get("points") or []) if scene else 0
    return {"ok": True, "slug": slug, "point_count": point_count, "active": slug}


def list_scenes() -> ListMapsResult:
    data = dreamscape_db.list_scenes()
    scenes: list[SceneSummary] = [
        {
            "slug": str(s["slug"]),
            "title": str(s["title"]),
            "alt_title": str(s.get("alt_title", "")),
            "alt_titles": [str(x) for x in (s.get("alt_titles") or [])],
            "source_image": str(s["source_image"]),
            "point_count": int(s["point_count"]),
            "active": bool(s["active"]),
            "archived": bool(s.get("archived", False)),
            "season": int(s.get("season", 1)),
        }
        for s in data["scenes"]
    ]
    return {"active": str(data["active"]), "scenes": scenes}


def get_scene(slug: str) -> SceneDetail:
    slug = _clean_slug(slug)
    scene = dreamscape_db.get_scene(slug)
    if scene is None:
        msg = f"unknown scene: {slug}"
        raise FileNotFoundError(msg)
    rect = scene.get("scene_rect")
    points: list[ScenePoint] = [
        {
            "n": int(p.get("n", 0)),
            "name": str(p.get("name", "")),
            "xPct": float(p.get("xPct", 0.0)),
            "yPct": float(p.get("yPct", 0.0)),
        }
        for p in (scene.get("points") or [])
    ]
    points.sort(key=lambda p: p["n"])
    return {
        "slug": slug,
        "title": str(scene.get("title") or slug),
        "alt_title": str(scene.get("alt_title") or ""),
        "alt_titles": [str(x) for x in (scene.get("alt_titles") or [])],
        "source_image": str(scene.get("source_image") or ""),
        "images": [str(x) for x in (scene.get("images") or []) if str(x).strip()],
        "scene_rect": _coerce_rect(rect) if isinstance(rect, dict) else None,
        "points": points,
        "active": bool(scene.get("active")),
        "archived": bool(scene.get("archived", False)),
        "season": int(scene.get("season", 1)),
    }
