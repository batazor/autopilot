"""Module-owned SQLite store for Dreamscape Memory scene maps.

The Dreamscape solver needs per-scene item locations ("where does WORD live in
the active scene?"). These used to live in the module's ``map.yaml``; they now
live in a **module-level** database so operators get a single, queryable,
atomically-written source of truth that ships with the module.

The DB file sits next to the module (``games/wos/events/dreamscape_memory/
scenes.db``) and is the **committed source of truth**: scenes are seeded once
via ``tools/import_maps*.py``, then hand-edited by operators (points, galleries,
the practice-level scene), and those edits live only here — so the ``.db`` is
tracked in git. Only the transient WAL/SHM sidecars are ignored; checkpoint the
WAL (``PRAGMA wal_checkpoint(TRUNCATE)``) before committing so the ``.db`` is
self-contained. This access code lives here so both the worker solver
(``exec.py``) and the onboarding API can import it. It reuses the shared
per-path WAL engine from :mod:`config.orm` (the same machinery behind
``state.db`` / ``giftcodes_db``), just pointed at its own file.

One row per scene; exactly one row is ``active`` (the scene the solver taps).
``points`` and ``scene_rect`` are stored as JSON. On first use, a legacy
``map.yaml`` (if still present) is imported once.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from sqlmodel import Field, Session, SQLModel, select

from config import orm
from config.paths import repo_root
from ocr.word_cleaning import is_plausible_word_text, normalize_word_text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_MODULE_REL = "games/wos/events/dreamscape_memory"
_LEGACY_MAP_REL = f"{_MODULE_REL}/map.yaml"
_DB_FILENAME = "scenes.db"

# ``season`` doubles as the Guides category. Numbered content seasons are
# 1, 2, 3, …; these reserved values carve out the non-numbered buckets.
SEASON_PRACTICE = 0  # practice/test scenes (out of rotation)
SEASON_MULTIPLAYER = 100  # co-op Multiplayer (Recall Road) scenes

_path_override: Path | None = None


def db_path() -> Path:
    """Module-level DB path (test-overridable)."""
    if _path_override is not None:
        return _path_override
    return repo_root() / _MODULE_REL / _DB_FILENAME


def set_db_path_for_tests(path: Path | None) -> None:
    global _path_override
    _path_override = path
    orm.reset_for_tests()


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class DreamscapeSceneRow(SQLModel, table=True):
    __tablename__ = "dreamscape_scenes"

    slug: str = Field(primary_key=True)
    title: str = ""
    # Optional matching alias: an alternate on-screen level name the OCR'd title
    # can resolve to (some rooms are labeled differently in-game than their
    # ``title``). Empty = no alias. Used alongside ``title`` in scene matching.
    # Legacy single-value field; superseded by ``alt_titles_json`` (kept in sync
    # to the first alias so older readers still resolve).
    alt_title: str = ""
    # JSON: ["Backyard", "Patio", ...] — the full alias list. A scene can carry
    # several alternate on-screen names (different in-game labels for one room).
    # Empty list = no aliases; legacy rows fall back to ``alt_title``.
    alt_titles_json: str = Field(default="[]")
    source_image: str = ""
    # JSON: ["games/.../a.png", ...] — extra reference images for a multi-shot
    # scene (e.g. the Multiplayer "Monument"). ``source_image`` is the primary /
    # item-mapped image; ``images`` is the full gallery (primary first). Empty =
    # single-image scene (the UI falls back to ``[source_image]``).
    images_json: str = Field(default="[]")
    # JSON: {"left","top","width","height"} or null.
    scene_rect_json: str | None = Field(default=None)
    # JSON: [{"n","name","xPct","yPct"}, ...].
    points_json: str = Field(default="[]")
    active: bool = Field(default=False)
    # Event-rotation status: True = retired/off-rotation scene (kept reusable),
    # False = current rotation. Distinct from ``active`` (the single scene the
    # bot is solving right now).
    archived: bool = Field(default=False)
    # Content batch / Guides category: 0 = practice/test, 1 = Season 1
    # (wostools), 2 = Season 2, 3 = Season 3, 100 = Multiplayer (Recall Road).
    # See ``SEASON_PRACTICE`` / ``SEASON_MULTIPLAYER``. Independent of
    # ``archived`` (rotation status).
    season: int = Field(default=1)
    updated_at: float = 0.0


# ---------------------------------------------------------------------------
# schema / engine
# ---------------------------------------------------------------------------


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=[DreamscapeSceneRow.__table__])
    _add_missing_columns(engine)
    _import_legacy_map_yaml(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Idempotent column adds for tables that predate a new field."""
    with engine.begin() as conn:
        cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(dreamscape_scenes)")
        }
        if "archived" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dreamscape_scenes "
                "ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"
            )
        if "season" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dreamscape_scenes ADD COLUMN season INTEGER NOT NULL DEFAULT 1"
            )
        if "images_json" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dreamscape_scenes "
                "ADD COLUMN images_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "alt_title" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dreamscape_scenes "
                "ADD COLUMN alt_title VARCHAR NOT NULL DEFAULT ''"
            )
        if "alt_titles_json" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dreamscape_scenes "
                "ADD COLUMN alt_titles_json TEXT NOT NULL DEFAULT '[]'"
            )


def _engine() -> Engine:
    engine = orm.get_engine(db_path())
    orm.ensure_once(engine, "dreamscape_scenes", _ensure_schema)
    return engine


def _import_legacy_map_yaml(engine: Engine) -> None:
    """One-time import of a pre-existing ``map.yaml`` into an empty table."""
    with Session(engine) as s:
        if s.exec(select(DreamscapeSceneRow).limit(1)).first() is not None:
            return
    legacy = repo_root() / _LEGACY_MAP_REL
    if not legacy.is_file():
        return
    try:
        import yaml

        doc = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("dreamscape_db: failed to read legacy map %s", legacy)
        return
    scenes = doc.get("scenes") if isinstance(doc, dict) else None
    if not isinstance(scenes, dict) or not scenes:
        return
    active = str(doc.get("active") or "").strip()
    now = time.time()
    with Session(engine) as s:
        for slug, scene in scenes.items():
            if not isinstance(scene, dict):
                continue
            points = _legacy_points_to_list(scene.get("points"))
            s.add(
                DreamscapeSceneRow(
                    slug=str(slug),
                    title=str(scene.get("title") or slug),
                    source_image=str(scene.get("source_image") or ""),
                    scene_rect_json=_dump_rect(scene.get("scene_rect")),
                    points_json=json.dumps(points),
                    active=(str(slug) == active),
                    updated_at=now,
                )
            )
        s.commit()
    logger.info("dreamscape_db: imported %d legacy scene(s) from %s", len(scenes), legacy)


def _legacy_points_to_list(points: Any) -> list[dict[str, Any]]:
    """Map the legacy ``{name: {x, y, n}}`` form to ``[{n,name,xPct,yPct}]``."""
    out: list[dict[str, Any]] = []
    if isinstance(points, dict):
        for name, coord in points.items():
            if not isinstance(coord, dict):
                continue
            try:
                out.append(
                    {
                        "n": int(coord.get("n", 0)),
                        "name": str(name),
                        "xPct": float(coord["x"]),
                        "yPct": float(coord["y"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    out.sort(key=lambda p: p["n"])
    return out


# ---------------------------------------------------------------------------
# (de)serialization helpers
# ---------------------------------------------------------------------------


def _dump_rect(rect: Any) -> str | None:
    if not isinstance(rect, dict):
        return None
    try:
        return json.dumps(
            {
                "left": float(rect["left"]),
                "top": float(rect["top"]),
                "width": float(rect["width"]),
                "height": float(rect["height"]),
            }
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _clean_alias_list(values: Any) -> list[str]:
    """Trim/collapse whitespace, drop blanks, and de-dupe (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return out
    for value in values:
        text = " ".join(str(value).split())
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _alt_titles(row: DreamscapeSceneRow) -> list[str]:
    """Alias list for a row, falling back to the legacy single ``alt_title``."""
    aliases = _clean_alias_list(_load_json(row.alt_titles_json, []))
    if not aliases and row.alt_title.strip():
        return [row.alt_title.strip()]
    return aliases


def _row_to_detail(row: DreamscapeSceneRow) -> dict[str, Any]:
    aliases = _alt_titles(row)
    return {
        "slug": row.slug,
        "title": row.title or row.slug,
        "alt_title": aliases[0] if aliases else "",
        "alt_titles": aliases,
        "source_image": row.source_image,
        "images": _load_json(row.images_json, []),
        "scene_rect": _load_json(row.scene_rect_json, None),
        "points": _load_json(row.points_json, []),
        "active": bool(row.active),
        "archived": bool(row.archived),
        "season": int(row.season),
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def upsert_scene(
    slug: str,
    *,
    title: str,
    source_image: str,
    scene_rect: dict[str, Any] | None,
    points: list[dict[str, Any]],
    activate: bool,
    archived: bool | None = None,
    season: int | None = None,
    images: list[str] | None = None,
    alt_title: str | None = None,
    alt_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Insert/replace a scene; when ``activate`` make it the sole active scene.

    ``archived``/``season``/``images``/``alt_title``/``alt_titles`` are left
    untouched on update when ``None`` (so re-imports and rect/point saves don't
    reset an operator's rotation/season tagging, gallery, or matching aliases);
    new rows default to not-archived, Season 1, single-image, no aliases.

    ``alt_titles`` (the full alias list) takes precedence over the legacy
    single-value ``alt_title``; the ``alt_title`` column is kept in sync with the
    first alias so older readers still resolve.
    """
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        if row is None:
            row = DreamscapeSceneRow(slug=slug)
            s.add(row)
        row.title = title or slug
        if alt_titles is not None:
            cleaned = _clean_alias_list(alt_titles)
            row.alt_titles_json = json.dumps(cleaned)
            row.alt_title = cleaned[0] if cleaned else ""
        elif alt_title is not None:
            alias = alt_title.strip()
            row.alt_title = alias
            row.alt_titles_json = json.dumps([alias] if alias else [])
        row.source_image = source_image
        if images is not None:
            row.images_json = json.dumps([str(x) for x in images])
        row.scene_rect_json = _dump_rect(scene_rect)
        row.points_json = json.dumps(points)
        if archived is not None:
            row.archived = bool(archived)
        if season is not None:
            row.season = int(season)
        row.updated_at = now
        if activate:
            for other in s.exec(
                select(DreamscapeSceneRow).where(DreamscapeSceneRow.active)
            ).all():
                if other.slug != slug:
                    other.active = False
            row.active = True
        s.commit()
        active = _active_slug(s)
    return {"ok": True, "slug": slug, "point_count": len(points), "active": active}


def _active_slug(s: Session) -> str:
    row = s.exec(select(DreamscapeSceneRow).where(DreamscapeSceneRow.active)).first()
    return row.slug if row is not None else ""


def list_scenes() -> dict[str, Any]:
    with Session(_engine()) as s:
        rows = s.exec(select(DreamscapeSceneRow)).all()
        active = _active_slug(s)
    scenes = []
    for r in rows:
        aliases = _alt_titles(r)
        scenes.append(
            {
                "slug": r.slug,
                "title": r.title or r.slug,
                "alt_title": aliases[0] if aliases else "",
                "alt_titles": aliases,
                "source_image": r.source_image,
                "point_count": len(_load_json(r.points_json, [])),
                "active": bool(r.active),
                "archived": bool(r.archived),
                "season": int(r.season),
            }
        )
    scenes.sort(key=lambda d: d["slug"])
    return {"active": active, "scenes": scenes}


def scene_word_index() -> dict[str, Any]:
    """All scenes with their item-name lists, for word-based scene detection.

    Unlike :func:`list_scenes` (summaries), this carries each scene's full set of
    mapped item names so the detector can match the on-screen words against them.
    """
    with Session(_engine()) as s:
        rows = s.exec(select(DreamscapeSceneRow)).all()
        active = _active_slug(s)
    scenes = [
        {
            "slug": r.slug,
            "title": r.title or r.slug,
            "season": int(r.season),
            "active": bool(r.active),
            "archived": bool(r.archived),
            "names": [
                str(p.get("name", ""))
                for p in _load_json(r.points_json, [])
                if isinstance(p, dict)
            ],
        }
        for r in rows
    ]
    scenes.sort(key=lambda d: d["slug"])
    return {"active": active, "scenes": scenes}


# Min letters for a word to count toward scene detection (mirrors the solver's
# unmapped-word gate). Garbage shorter than this is ignored before counting.
_MIN_DETECT_WORD_LETTERS = 3


def match_scene_by_words(
    words: list[str],
    scenes: list[dict[str, Any]],
    *,
    prefer_season: int | None = None,
    min_letters: int = _MIN_DETECT_WORD_LETTERS,
) -> str | None:
    """Best scene slug for the set of on-screen item words.

    The on-screen title is unreliable, so a scene is identified by *which scene
    contains the words shown*. We require the strongest overlap first and relax it
    only when nothing matches: with three readable words, demand a scene holding
    all three; if none, two; otherwise one. OCR garbage (too short / noise) is
    dropped before counting, so a junk slot just lowers the bar instead of
    mis-matching. A room reused across seasons breaks toward ``prefer_season``,
    then the highest season.

    ``scenes`` are the entries from :func:`scene_word_index` (each with a
    ``names`` list and ``season``).
    """
    known_names = {
        normalize_word_text(name)
        for scene in scenes
        for name in scene.get("names", [])
        if normalize_word_text(name)
    }
    detected: list[str] = []
    seen: set[str] = set()
    for raw in words:
        key = normalize_word_text(raw)
        if not key:
            continue
        if key not in known_names and not is_plausible_word_text(
            raw, min_letters=min_letters
        ):
            continue
        if key and key not in seen:
            seen.add(key)
            detected.append(key)
    if not detected or not scenes:
        return None

    def rank(scene: dict[str, Any]) -> tuple[int, int]:
        season = int(scene.get("season") or 0)
        return (1 if season == prefer_season else 0, season)

    scored: list[tuple[int, dict[str, Any]]] = []
    for scene in scenes:
        names = {normalize_word_text(n) for n in scene.get("names", [])}
        overlap = sum(1 for key in detected if key in names)
        if overlap:
            scored.append((overlap, scene))
    if not scored:
        return None

    for need in range(min(len(detected), 3), 0, -1):
        bucket = [(overlap, scene) for overlap, scene in scored if overlap >= need]
        if bucket:
            _overlap, scene = max(bucket, key=lambda item: (item[0], rank(item[1])))
            return str(scene["slug"])
    return None


def detect_scene_by_words(words: list[str]) -> dict[str, Any] | None:
    """Auto-detect the scene from the on-screen item words (no active fallback).

    Returns the full scene detail (as :func:`get_scene`) or ``None`` when nothing
    matches. The active scene only supplies the preferred season for same-name
    tie-breaks — it is *not* returned as a fallback, so callers can distinguish a
    real detection from "nothing recognised".
    """
    index = scene_word_index()
    active = get_active_scene()
    prefer = int(active["season"]) if active and "season" in active else None
    slug = match_scene_by_words(words, index["scenes"], prefer_season=prefer)
    return get_scene(slug) if slug else None


def set_active(slug: str) -> bool:
    """Make ``slug`` the sole active scene (the one the solver taps).

    Returns True if the scene exists. Clears ``active`` on every other row so the
    invariant "exactly one active scene" holds.
    """
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        if row is None:
            return False
        for other in s.exec(
            select(DreamscapeSceneRow).where(DreamscapeSceneRow.active)
        ).all():
            if other.slug != slug:
                other.active = False
        row.active = True
        row.updated_at = time.time()
        s.commit()
    return True


def set_archived(slug: str, archived: bool) -> bool:
    """Set a scene's rotation status. Returns True if the scene existed."""
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        if row is None:
            return False
        row.archived = bool(archived)
        row.updated_at = time.time()
        s.commit()
    return True


def set_season(slug: str, season: int) -> bool:
    """Set a scene's content season. Returns True if the scene existed."""
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        if row is None:
            return False
        row.season = int(season)
        row.updated_at = time.time()
        s.commit()
    return True


def get_scene(slug: str) -> dict[str, Any] | None:
    with Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        return _row_to_detail(row) if row is not None else None


def get_active_scene() -> dict[str, Any] | None:
    """The scene the solver should tap (or ``None`` when none is active)."""
    with Session(_engine()) as s:
        row = s.exec(
            select(DreamscapeSceneRow).where(DreamscapeSceneRow.active)
        ).first()
        return _row_to_detail(row) if row is not None else None


def delete_scene(slug: str) -> bool:
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DreamscapeSceneRow, slug)
        if row is None:
            return False
        s.delete(row)
        s.commit()
    return True
