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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz, process

from layout.types import Point
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

# Pause between taps so each one settles before the next.
_DEFAULT_TAP_DELAY_S = 0.6

# Minimum rapidfuzz WRatio (0–100) for an OCR'd word to be corrected to a mapped
# item when the exact normalized key misses. OCR garbles characters ("Lightening"
# for "Lightning", "Snowmann" for "Snowman"); fuzzy recovery taps them anyway.
# High enough to keep near-collisions (e.g. "Cart"/"Cat") apart. Override per-step
# with ``fuzz_threshold:`` on the ``exec:`` step; ``0`` disables fuzzy matching.
_DEFAULT_FUZZ_THRESHOLD = 88.0


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────


def _normalize_word(raw: object) -> str:
    """Lower-case, trim, and collapse inner whitespace for stable map keys."""
    return " ".join(str(raw or "").split()).lower()


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
    hits: list[tuple[str, Point]] = []
    misses: list[str] = []
    choices = list(targets)
    for word in words:
        key = _normalize_word(word)
        if not key:
            continue
        coord = targets.get(key)
        if coord is None:
            matched = _fuzzy_key(key, choices, fuzz_threshold)
            if matched is not None:
                logger.info(
                    "dreamscape_memory_solve: fuzzy-matched %r -> %r", word, matched
                )
                coord = targets[matched]
        if coord is None:
            misses.append(word)
            continue
        x_pct, y_pct = coord
        point = Point(
            int(round(x_pct / 100.0 * dev_w)),
            int(round(y_pct / 100.0 * dev_h)),
        )
        hits.append((word, point))
    return hits, misses


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

    targets = _load_targets()
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

    ctx.result.update({"words": words, "tapped": tapped, "unmapped": misses})


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = {
    "dreamscape_memory_solve": _exec_dreamscape_memory_solve,
}
