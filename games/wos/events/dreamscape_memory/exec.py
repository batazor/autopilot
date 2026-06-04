"""DSL ``exec:`` handler for the Dreamscape Memory recall-road levels.

The scenario OCRs the word buttons at the bottom of a level
(``dreamscape_memory.1`` / ``.2`` / ``.3``) into Redis, then calls
``exec: dreamscape_memory_solve``. This handler reads those words back, looks
each one up in the active scene's map (word -> scene coordinate, from the module
scene DB :mod:`config.dreamscape_db`) and taps the matching spot in the scene.
Lookup is exact-first, then fuzzy (``fuzz_threshold``) so OCR character errors
still resolve to the intended item.

Words with no exact or fuzzy map entry are logged and surfaced on ``ctx.result``
as ``unmapped`` so the operator knows what to add via the onboarding flow.

Discovered automatically by ``config.module_exec_registry`` (a module exec.py
with a ``DSL_EXEC_HANDLERS`` dict needs no wiring in ``module.yaml``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from rapidfuzz import fuzz, process

from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Point, Region
from ocr.preprocess import resolve_preprocess
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec import DslExecContext

DslExecHandler = Callable[[Any], Awaitable[None]]

logger = logging.getLogger(__name__)

# Default OCR regions to read, in tap order. Override per-step with
# ``regions: [ ... ]`` on the ``exec:`` step.
_DEFAULT_REGIONS: tuple[str, ...] = (
    "dreamscape_memory.1",
    "dreamscape_memory.2",
    "dreamscape_memory.3",
)

# OCR region holding the on-screen level/scene name. Read to auto-select which
# scene map to solve (override per-step with ``level_region:``; empty disables
# name matching and falls back to the operator's active scene).
_DEFAULT_LEVEL_REGION = "dreamscape_memory.level.name"
_DEFAULT_HELP_REGION = "dreamscape_memory.help"
_DEFAULT_HELP_COUNTER_REGION = "dreamscape_memory.help.counter"
_DEFAULT_HELP_COUNT = 2

_DEFAULT_MULTIPLAYER_REGIONS: tuple[str, ...] = (
    "dreamscape_memory_.multiplayer.1",
    "dreamscape_memory_.multiplayer.2",
    "dreamscape_memory_.multiplayer.3",
    "dreamscape_memory_.multiplayer.4",
    "dreamscape_memory_.multiplayer.5",
    "dreamscape_memory_.multiplayer.6",
)

# Strip the season tag from a title ("Aquarium (S3)") / slug ("aquarium-s3") so
# it matches the bare on-screen level name ("Aquarium").
_SEASON_TAG_RE = re.compile(r"\s*\(s\d+\)\s*$", re.IGNORECASE)
_SLUG_SUFFIX_RE = re.compile(r"-(?:s\d+|mp)$", re.IGNORECASE)
_LEVEL_PROGRESS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%.*$", re.IGNORECASE)

# Pause between taps so each one settles before the next.
_DEFAULT_TAP_DELAY_S = 0.6
_DEFAULT_LOOP_TTL_S = 5 * 60.0
_DEFAULT_LOOP_WAIT_S = 0.3
_DEFAULT_LOOP_MAX_ITERATIONS = 3000
_START_SCREEN = "dreamscape_memory"
_TERMINAL_TIME_UP = "dreamscape_memory.time_up"
_TERMINAL_ALL_FOUND = "dreamscape_memory.all_item_found"
_TERMINAL_SCREENS = frozenset({_START_SCREEN, _TERMINAL_TIME_UP, _TERMINAL_ALL_FOUND})

# Minimum rapidfuzz WRatio (0–100) for an OCR'd word to be corrected to a mapped
# item when the exact normalized key misses. OCR garbles characters ("Lightening"
# for "Lightning", "Snowmann" for "Snowman"); fuzzy recovery taps them anyway.
# High enough to keep near-collisions (e.g. "Cart"/"Cat") apart. Override per-step
# with ``fuzz_threshold:`` on the ``exec:`` step; ``0`` disables fuzzy matching.
_DEFAULT_FUZZ_THRESHOLD = 88.0


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────


class TapCandidate(NamedTuple):
    raw_word: str
    key: str
    point: Point


def _normalize_word(raw: object) -> str:
    """Lower-case, trim, and collapse inner whitespace for stable map keys."""
    return " ".join(str(raw or "").split()).lower()


def _normalize_level_name(raw: object) -> str:
    """Normalize OCR'd level titles for scene lookup.

    Dreamscape title OCR often sees separators/progress as text
    (``Practice|Level · 23%``). For scene selection, keep only searchable
    alphanumeric words so that noisy UI chrome does not block an exact match.
    """
    s = str(raw or "").casefold()
    s = _LEVEL_PROGRESS_RE.sub(" ", s)
    s = re.sub(r"(?<=[a-z])[\|/\\]+(?=[a-z])", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _parse_help_counter(raw: object) -> int | None:
    match = re.search(r"\d+", str(raw or ""))
    if match is None:
        return None
    try:
        return max(0, int(match.group(0)))
    except ValueError:
        return None


def _scene_rect(raw: object) -> tuple[float, float, float, float] | None:
    """Parse a ``scene_rect`` (% of game frame) into ``(left, top, w, h)``.

    Returns ``None`` (identity mapping) when absent or malformed — the points
    are then taken as direct game-frame percentages.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return (
            float(raw["left"]),
            float(raw["top"]),
            float(raw["width"]),
            float(raw["height"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("dreamscape_memory_solve: skipping malformed scene_rect %r", raw)
        return None


def _points_to_targets(
    points: object,
    scene_rect: tuple[float, float, float, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """Parse ``[{n, name, xPct, yPct}]`` into ``{normalized_word: (x_pct, y_pct)}``.

    Coordinates are guide-image percentages; when ``scene_rect`` (where the
    scene art sits in the 720x1280 game frame) is given they are mapped into
    game-frame percentages: ``frame = rect_origin + guide/100 * rect_size``.
    With no rect the points are used as-is. Malformed entries are skipped and
    logged rather than aborting the whole solve.
    """
    if not isinstance(points, list):
        return {}
    out: dict[str, tuple[float, float]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        key = _normalize_word(point.get("name"))
        if not key:
            continue
        try:
            x_pct = float(point["xPct"])
            y_pct = float(point["yPct"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dreamscape_memory_solve: skipping malformed point %r", point
            )
            continue
        if scene_rect is not None:
            left, top, width, height = scene_rect
            x_pct = left + x_pct / 100.0 * width
            y_pct = top + y_pct / 100.0 * height
        out[key] = (x_pct, y_pct)
    return out


def _load_targets() -> dict[str, tuple[float, float]]:
    """Load the active scene's ``{normalized_word: (x_pct, y_pct)}`` from the DB.

    Scene maps live in the module's scene database (:mod:`config.dreamscape_db`);
    exactly one scene is active. No active scene → empty targets (safe no-op).
    """
    from config.dreamscape_db import get_active_scene

    scene = get_active_scene()
    if not scene:
        return {}
    return _points_to_targets(scene.get("points"), _scene_rect(scene.get("scene_rect")))


def _targets_for_scene(scene: dict[str, Any] | None) -> dict[str, tuple[float, float]]:
    """``{normalized_word: (x_pct, y_pct)}`` for a scene detail (empty if None)."""
    if not scene:
        return {}
    return _points_to_targets(scene.get("points"), _scene_rect(scene.get("scene_rect")))


def _scene_base_name(title: object, slug: object) -> str:
    """Bare room name for matching the on-screen level name.

    Titles carry a season tag ("Aquarium (S3)") and slugs a "-s2"/"-s3"/"-mp"
    suffix; the OCR'd level name is the plain room name ("Aquarium").
    """
    name = _SEASON_TAG_RE.sub("", str(title or "")).strip()
    if name:
        return name
    return _SLUG_SUFFIX_RE.sub("", str(slug or "")).replace("-", " ").strip()


def _match_scene_slug(
    level_name: str,
    scenes: list[dict[str, Any]],
    *,
    prefer_season: int | None = None,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> str | None:
    """Best scene slug for an OCR'd level name (exact-then-fuzzy on room name).

    A room reused across seasons (e.g. "Aquarium" in Season 1 and Season 3) is a
    tie; it breaks toward ``prefer_season`` (the live event, from the active
    scene), then the highest season number.
    """
    key = _normalize_level_name(level_name)
    if not key or not scenes:
        return None

    def rank(scene: dict[str, Any]) -> tuple[int, int]:
        season = int(scene.get("season") or 0)
        return (1 if season == prefer_season else 0, season)

    by_base: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        base = _normalize_level_name(_scene_base_name(scene.get("title"), scene.get("slug")))
        by_base.setdefault(base, []).append(scene)

    bucket = by_base.get(key) or (
        by_base.get(_fuzzy_key(key, list(by_base), fuzz_threshold) or "") or []
    )
    if not bucket:
        return None
    return str(max(bucket, key=rank)["slug"])


def _select_scene(level_name: str, fuzz_threshold: float) -> dict[str, Any] | None:
    """Scene to solve: match the OCR'd level name, else the operator's active scene.

    The active scene also supplies the preferred season (the live event) used to
    break same-name ties; with no level name we keep the active scene as-is.
    """
    from config import dreamscape_db

    active = dreamscape_db.get_active_scene()
    if not level_name.strip():
        return active

    listing = dreamscape_db.list_scenes()
    prefer = int(active["season"]) if active and "season" in active else None
    slug = _match_scene_slug(
        level_name, listing["scenes"], prefer_season=prefer, fuzz_threshold=fuzz_threshold
    )
    if slug:
        scene = dreamscape_db.get_scene(slug)
        if scene:
            logger.info(
                "dreamscape_memory_solve: level %r -> scene %r (season %s)",
                level_name,
                slug,
                scene.get("season"),
            )
            return scene
    logger.warning(
        "dreamscape_memory_solve: level %r matched no scene; using active scene %r",
        level_name,
        active.get("slug") if active else None,
    )
    return active


def _fuzzy_key(
    key: str,
    choices: list[str],
    threshold: float,
) -> str | None:
    """Best fuzzy match for ``key`` among ``choices`` at/above ``threshold``.

    Recovers from OCR noise (a swapped/dropped character) when the exact key
    misses. Returns the matched choice, or ``None`` when fuzzy matching is off
    (``threshold <= 0``), there are no choices, or nothing clears the cutoff.
    """
    if threshold <= 0 or not choices:
        return None
    match = process.extractOne(
        key, choices, scorer=fuzz.WRatio, score_cutoff=threshold
    )
    return match[0] if match is not None else None


def _resolve_taps(
    words: list[str],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
    *,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> tuple[list[tuple[str, Point]], list[str]]:
    """Split OCR'd words into (word, tap-point) hits and unmapped misses.

    An exact normalized-key lookup is tried first; on a miss the word is fuzzy
    matched against the mapped item names (``fuzz_threshold``, 0 disables) to
    absorb OCR character errors. Percentage coordinates are converted to device
    pixels the same way the DSL click step does: ``px = pct / 100 * dimension``.
    """
    candidates, misses = _resolve_tap_candidates(
        words, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
    )
    return [(c.raw_word, c.point) for c in candidates], misses


def _resolve_tap_candidates(
    words: list[str],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
    *,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> tuple[list[TapCandidate], list[str]]:
    """Resolve OCR'd words and keep the canonical target key for de-duping."""
    candidates: list[TapCandidate] = []
    misses: list[str] = []
    choices = list(targets)
    for word in words:
        key = _normalize_word(word)
        if not key:
            continue
        coord = targets.get(key)
        target_key = key
        if coord is None:
            matched = _fuzzy_key(key, choices, fuzz_threshold)
            if matched is not None:
                logger.info(
                    "dreamscape_memory_solve: fuzzy-matched %r -> %r", word, matched
                )
                coord = targets[matched]
                target_key = matched
        if coord is None:
            misses.append(word)
            continue
        x_pct, y_pct = coord
        point = Point(
            int(round(x_pct / 100.0 * dev_w)),
            int(round(y_pct / 100.0 * dev_h)),
        )
        candidates.append(TapCandidate(raw_word=word, key=target_key, point=point))
    return candidates, misses


def _parse_duration_s(raw: object, default: float) -> float:
    """Parse DSL-ish durations (``300ms``, ``5m``, number seconds)."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    s = str(raw or "").strip().lower()
    if not s:
        return default
    try:
        if s.endswith("ms"):
            return max(0.0, float(s[:-2].strip()) / 1000.0)
        if s.endswith("s"):
            return max(0.0, float(s[:-1].strip()))
        if s.endswith("m"):
            return max(0.0, float(s[:-1].strip()) * 60.0)
        if s.endswith("h"):
            return max(0.0, float(s[:-1].strip()) * 3600.0)
        return max(0.0, float(s))
    except ValueError:
        return default


def _load_area() -> dict[str, Any]:
    return load_area_doc(repo_root())


def _region_to_px(region_def: dict[str, Any], frame_w: int, frame_h: int) -> Region | None:
    bbox = region_def.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = int(round(float(bbox["x"]) / 100.0 * frame_w))
        y = int(round(float(bbox["y"]) / 100.0 * frame_h))
        w = int(round(float(bbox["width"]) / 100.0 * frame_w))
        h = int(round(float(bbox["height"]) / 100.0 * frame_h))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return Region(x, y, w, h)


def _threshold(region_def: dict[str, Any]) -> float:
    try:
        return float(region_def.get("threshold", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _region_center_for_frame(
    area_doc: dict[str, Any],
    name: str,
    frame_w: int,
    frame_h: int,
) -> Point | None:
    pair = screen_region_by_name(area_doc, name)
    region_def = pair[1] if pair else None
    if not isinstance(region_def, dict):
        return None
    px = _region_to_px(region_def, frame_w, frame_h)
    return px.center() if px is not None else None


async def _ocr_current_frame(
    image: Any,
    area_doc: dict[str, Any],
    names: list[str],
) -> dict[str, str]:
    """OCR named regions from the current frame, skipping low-confidence reads."""
    if image is None or not hasattr(image, "shape"):
        return {}
    frame_h, frame_w = int(image.shape[0]), int(image.shape[1])

    regions: list[Region] = []
    ids: list[str] = []
    preprocess: list[str | None] = []
    thresholds: dict[str, float] = {}
    for name in names:
        pair = screen_region_by_name(area_doc, name)
        region_def = pair[1] if pair else None
        if not isinstance(region_def, dict):
            logger.warning("dreamscape_memory_solve_loop: OCR region not found: %s", name)
            continue
        px = _region_to_px(region_def, frame_w, frame_h)
        if px is None:
            logger.warning("dreamscape_memory_solve_loop: OCR region malformed: %s", name)
            continue
        regions.append(px)
        ids.append(name)
        preprocess.append(
            resolve_preprocess(
                explicit=region_def.get("preprocess"),
                type_hint=region_def.get("type"),
            )
        )
        thresholds[name] = _threshold(region_def)

    if not regions:
        return {}

    results = await dsl_runtime.ocr_client().ocr_regions(
        image,
        regions,
        region_ids=ids,
        region_preprocess=preprocess if any(preprocess) else None,
    )

    out: dict[str, str] = {}
    for result in results:
        rid = str(result.region_id or "").strip()
        text = str(result.text or "").strip()
        confidence = float(result.confidence or 0.0)
        if not rid or not text:
            continue
        if confidence < thresholds.get(rid, 0.0):
            logger.debug(
                "dreamscape_memory_solve_loop: low-confidence OCR region=%s "
                "text=%r confidence=%.3f threshold=%.3f",
                rid,
                text,
                confidence,
                thresholds.get(rid, 0.0),
            )
            continue
        out[rid] = text
    return out


# ── Redis IO ────────────────────────────────────────────────────────────────


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


async def _resolve_player_id(ctx: DslExecContext) -> str:
    pid = str(getattr(ctx, "player_id", "") or "").strip()
    if pid or ctx.redis_client is None:
        return pid
    raw = await ctx.redis_client.hget(
        f"wos:instance:{ctx.instance_id}:state", "active_player"
    )
    return _decode(raw)


async def _read_word(ctx: DslExecContext, player_id: str, field: str) -> str:
    """Read an OCR'd region value, preferring player state, then instance."""
    if ctx.redis_client is None:
        return ""
    keys = []
    if player_id:
        keys.append(f"wos:player:{player_id}:state")
    keys.append(f"wos:instance:{ctx.instance_id}:state")
    for key in keys:
        text = _decode(await ctx.redis_client.hget(key, field))
        if text:
            return text
    return ""


# ── Handler ─────────────────────────────────────────────────────────────────


async def _exec_dreamscape_memory_solve(ctx: DslExecContext) -> None:
    args = ctx.args or {}
    regions = args.get("regions")
    if not isinstance(regions, list) or not regions:
        regions = list(_DEFAULT_REGIONS)
    try:
        tap_delay = float(args.get("tap_delay", _DEFAULT_TAP_DELAY_S))
    except (TypeError, ValueError):
        tap_delay = _DEFAULT_TAP_DELAY_S
    try:
        fuzz_threshold = float(args.get("fuzz_threshold", _DEFAULT_FUZZ_THRESHOLD))
    except (TypeError, ValueError):
        fuzz_threshold = _DEFAULT_FUZZ_THRESHOLD

    level_region = args.get("level_region", _DEFAULT_LEVEL_REGION)

    player_id = await _resolve_player_id(ctx)
    words = [await _read_word(ctx, player_id, str(r)) for r in regions]
    words = [w for w in words if w]
    if not words:
        logger.info(
            "dreamscape_memory_solve: no OCR words for regions %s (instance=%s)",
            regions,
            ctx.instance_id,
        )
        return

    # Auto-select the scene from the on-screen level name; the active scene is
    # the fallback (and supplies the live season for same-name tie-breaks).
    level_name = (
        await _read_word(ctx, player_id, str(level_region)) if level_region else ""
    )
    scene = await asyncio.to_thread(_select_scene, level_name, fuzz_threshold)
    targets = _targets_for_scene(scene)
    actions = dsl_runtime.bot_actions()
    dev_w, dev_h = await asyncio.to_thread(actions.screen_resolution, ctx.instance_id)
    hits, misses = _resolve_taps(
        words, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
    )

    tapped: list[str] = []
    for word, point in hits:
        ok = await asyncio.to_thread(actions.tap, ctx.instance_id, point)
        logger.info(
            "dreamscape_memory_solve: %s %r -> (%d,%d) instance=%s",
            "tapped" if ok else "tap-rejected",
            word,
            point.x,
            point.y,
            ctx.instance_id,
        )
        if ok:
            tapped.append(word)
            if tap_delay > 0:
                await asyncio.sleep(tap_delay)

    if misses:
        logger.warning(
            "dreamscape_memory_solve: %d unmapped word(s) — add via onboarding: %s",
            len(misses),
            ", ".join(misses),
        )

    ctx.result.update(
        {
            "scene": scene.get("slug") if scene else "",
            "level_name": level_name,
            "words": words,
            "tapped": tapped,
            "unmapped": misses,
        }
    )


def _solver_regions_from_args(args: dict[str, Any]) -> list[str]:
    raw_regions = args.get("regions")
    if isinstance(raw_regions, list) and raw_regions:
        return [str(r).strip() for r in raw_regions if str(r or "").strip()]
    mode = str(args.get("mode") or "").strip().lower()
    if mode in {"multiplayer", "mp", "coop", "co-op"}:
        return list(_DEFAULT_MULTIPLAYER_REGIONS)
    return list(_DEFAULT_REGIONS)


async def _capture_frame(actions: Any, instance_id: str) -> Any:
    cached = getattr(actions, "capture_screen_bgr_cached", None)
    if cached is not None:
        return await asyncio.to_thread(cached, instance_id, max_age_ms=150.0)
    return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)


async def _detect_terminal_screen(image: Any, hint: str | None = None) -> str:
    """Return a Dreamscape terminal screen id for ``image``, or empty string."""
    if image is None or not hasattr(image, "shape"):
        return ""
    try:
        from navigation.detector import suggest_node_for_image_sync
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: screen detector unavailable", exc_info=True)
        return ""

    try:
        detected = await asyncio.to_thread(suggest_node_for_image_sync, image, hint=hint)
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: terminal screen detect failed", exc_info=True)
        return ""
    screen = str(detected or "").strip()
    return screen if screen in _TERMINAL_SCREENS else ""


async def _write_current_screen(ctx: DslExecContext, screen: str) -> None:
    if not screen or ctx.redis_client is None:
        return
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state",
            "current_screen",
            screen,
        )
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: failed to write current_screen", exc_info=True)


async def _exec_dreamscape_memory_solve_loop(ctx: DslExecContext) -> None:
    """Stateful realtime Dreamscape solver.

    This replaces the YAML ``ocr`` loop for Dreamscape: each iteration captures a
    fresh frame, OCRs the title + word slots, resolves words against the current
    scene, and taps only target keys that have not already been clicked in this
    scene.
    """
    args = ctx.args or {}
    regions = _solver_regions_from_args(args)
    level_region = str(args.get("level_region") or _DEFAULT_LEVEL_REGION).strip()
    help_region = str(args.get("help_region", _DEFAULT_HELP_REGION) or "").strip()
    help_counter_region = str(
        args.get("help_counter_region", _DEFAULT_HELP_COUNTER_REGION) or ""
    ).strip()
    all_ocr_regions = ([level_region] if level_region else []) + regions
    if help_region and help_counter_region:
        all_ocr_regions.append(help_counter_region)

    ttl_s = _parse_duration_s(args.get("ttl"), _DEFAULT_LOOP_TTL_S)
    wait_s = _parse_duration_s(args.get("wait"), _DEFAULT_LOOP_WAIT_S)
    tap_delay_s = _parse_duration_s(args.get("tap_delay"), _DEFAULT_TAP_DELAY_S)
    try:
        max_iterations = int(args.get("max_iterations", args.get("max", _DEFAULT_LOOP_MAX_ITERATIONS)))
    except (TypeError, ValueError):
        max_iterations = _DEFAULT_LOOP_MAX_ITERATIONS
    max_iterations = max(1, max_iterations)
    try:
        fuzz_threshold = float(args.get("fuzz_threshold", _DEFAULT_FUZZ_THRESHOLD))
    except (TypeError, ValueError):
        fuzz_threshold = _DEFAULT_FUZZ_THRESHOLD

    area_doc = _load_area()
    actions = dsl_runtime.bot_actions()
    dev_w, dev_h = await asyncio.to_thread(actions.screen_resolution, ctx.instance_id)
    deadline = time.monotonic() + ttl_s if ttl_s > 0 else None

    last_scene_slug = ""
    seen_keys: set[str] = set()
    seen_words: list[str] = []
    clicked_keys: set[str] = set()
    clicked_words: list[str] = []
    helped_keys: set[str] = set()
    helped_words: list[str] = []
    help_counter_reads: list[int] = []
    help_remaining = _DEFAULT_HELP_COUNT
    unmapped: list[str] = []
    skipped_clicked: list[str] = []
    iterations = 0
    taps_total = 0
    last_level_name = ""
    terminal_screen = ""

    for iteration in range(max_iterations):
        if deadline is not None and time.monotonic() >= deadline:
            break
        iterations = iteration + 1

        try:
            image = await _capture_frame(actions, ctx.instance_id)
        except Exception:
            logger.exception(
                "dreamscape_memory_solve_loop: capture failed instance=%s",
                ctx.instance_id,
            )
            break

        terminal_screen = await _detect_terminal_screen(
            image,
            hint=terminal_screen or last_scene_slug or None,
        )
        if terminal_screen:
            if terminal_screen != _START_SCREEN or taps_total > 0:
                await _write_current_screen(ctx, terminal_screen)
                logger.info(
                    "dreamscape_memory_solve_loop: terminal screen detected %s; stopping instance=%s",
                    terminal_screen,
                    ctx.instance_id,
                )
                break
            terminal_screen = ""

        ocr_values = await _ocr_current_frame(image, area_doc, all_ocr_regions)
        level_name = ocr_values.get(level_region, "") if level_region else ""
        if level_name:
            last_level_name = level_name
        words = [ocr_values.get(region, "") for region in regions]
        words = [word for word in words if word]

        scene = await asyncio.to_thread(_select_scene, level_name, fuzz_threshold)
        scene_slug = str(scene.get("slug") or "") if scene else ""
        if scene_slug and scene_slug != last_scene_slug:
            if last_scene_slug:
                logger.info(
                    "dreamscape_memory_solve_loop: scene changed %s -> %s; reset clicked memory",
                    last_scene_slug,
                    scene_slug,
                )
            last_scene_slug = scene_slug
            seen_keys.clear()
            seen_words.clear()
            clicked_keys.clear()
            clicked_words.clear()
            helped_keys.clear()
            helped_words.clear()
            help_counter_reads.clear()
            help_remaining = _DEFAULT_HELP_COUNT
            skipped_clicked.clear()
            unmapped.clear()
        if help_region and help_counter_region:
            counter = _parse_help_counter(ocr_values.get(help_counter_region, ""))
            if counter is not None:
                help_remaining = min(help_remaining, counter)
                help_counter_reads.append(counter)

        for word in words:
            key = _normalize_word(word)
            if key and key not in seen_keys:
                seen_keys.add(key)
                seen_words.append(word)

        targets = _targets_for_scene(scene)
        candidates, misses = _resolve_tap_candidates(
            words, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
        )
        for miss in misses:
            if miss not in unmapped:
                unmapped.append(miss)

        for candidate in candidates:
            if candidate.key in clicked_keys:
                if candidate.raw_word not in skipped_clicked:
                    skipped_clicked.append(candidate.raw_word)
                continue
            ok = await asyncio.to_thread(
                actions.tap,
                ctx.instance_id,
                candidate.point,
                require_approval=False,
            )
            logger.info(
                "dreamscape_memory_solve_loop: %s %r key=%r -> (%d,%d) instance=%s",
                "tapped" if ok else "tap-rejected",
                candidate.raw_word,
                candidate.key,
                candidate.point.x,
                candidate.point.y,
                ctx.instance_id,
            )
            if not ok:
                continue
            clicked_keys.add(candidate.key)
            clicked_words.append(candidate.raw_word)
            taps_total += 1
            if tap_delay_s > 0:
                await asyncio.sleep(tap_delay_s)

        help_word = next(
            (
                miss
                for miss in misses
                if (key := _normalize_word(miss)) and key not in helped_keys
            ),
            "",
        )
        if help_word and help_region and help_remaining > 0:
            help_point = _region_center_for_frame(area_doc, help_region, dev_w, dev_h)
            help_key = _normalize_word(help_word)
            if help_point is None:
                logger.warning(
                    "dreamscape_memory_solve_loop: help region not found/malformed: %s",
                    help_region,
                )
                helped_keys.add(help_key)
            else:
                ok = await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    help_point,
                    require_approval=False,
                )
                logger.info(
                    "dreamscape_memory_solve_loop: %s help for unmapped %r -> (%d,%d) instance=%s",
                    "tapped" if ok else "help-tap-rejected",
                    help_word,
                    help_point.x,
                    help_point.y,
                    ctx.instance_id,
                )
                if ok:
                    helped_keys.add(help_key)
                    helped_words.append(help_word)
                    help_remaining = max(0, help_remaining - 1)
                    taps_total += 1
                    if tap_delay_s > 0:
                        await asyncio.sleep(tap_delay_s)
        elif help_word and help_region:
            logger.info(
                "dreamscape_memory_solve_loop: no help remaining for unmapped %r instance=%s",
                help_word,
                ctx.instance_id,
            )

        if wait_s > 0:
            await asyncio.sleep(wait_s)

    ctx.result.update(
        {
            "scene": last_scene_slug,
            "level_name": last_level_name,
            "regions": regions,
            "iterations": iterations,
            "seen": seen_words,
            "clicked": clicked_words,
            "clicked_keys": sorted(clicked_keys),
            "helped": helped_words,
            "helped_keys": sorted(helped_keys),
            "help_counter_reads": help_counter_reads,
            "help_remaining": help_remaining,
            "skipped_clicked": skipped_clicked,
            "taps": taps_total,
            "unmapped": unmapped,
            "terminal_screen": terminal_screen,
            "status": (
                "won"
                if terminal_screen in {_TERMINAL_ALL_FOUND, _START_SCREEN}
                else "lost"
                if terminal_screen == _TERMINAL_TIME_UP
                else "stopped"
            ),
        }
    )


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = {
    "dreamscape_memory_solve": _exec_dreamscape_memory_solve,
    "dreamscape_memory_solve_loop": _exec_dreamscape_memory_solve_loop,
}
