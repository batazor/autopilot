"""DSL ``exec:`` handler for the Dreamscape Memory recall-road levels.

The scenario OCRs the word buttons at the bottom of a level
(``dreamscape_memory.1`` / ``.2`` / ``.3``) into Redis, then calls
``exec: dreamscape_memory_solve``. This handler reads those words back, looks
each one up in ``map.yaml`` (word -> scene coordinate) and taps the matching
spot in the scene.

Words with no map entry are logged and surfaced on ``ctx.result`` as
``unmapped`` so the operator knows what to add to ``map.yaml``.

Discovered automatically by ``config.module_exec_registry`` (a module exec.py
with a ``DSL_EXEC_HANDLERS`` dict needs no wiring in ``module.yaml``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

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

_MAP_PATH = Path(__file__).with_name("map.yaml")


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────


def _normalize_word(raw: object) -> str:
    """Lower-case, trim, and collapse inner whitespace for stable map keys."""
    return " ".join(str(raw or "").split()).lower()


def _load_targets(path: Path = _MAP_PATH) -> dict[str, tuple[float, float]]:
    """Parse ``map.yaml`` into ``{normalized_word: (x_pct, y_pct)}``.

    Malformed or incomplete entries are skipped (and logged) rather than
    aborting the whole solve.
    """
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("dreamscape_memory_solve: map %s not found", path)
        return {}
    except yaml.YAMLError:
        logger.exception("dreamscape_memory_solve: map %s is invalid YAML", path)
        return {}

    targets = doc.get("targets") if isinstance(doc, dict) else None
    if not isinstance(targets, dict):
        return {}

    out: dict[str, tuple[float, float]] = {}
    for word, coord in targets.items():
        key = _normalize_word(word)
        if not key or not isinstance(coord, dict):
            continue
        try:
            out[key] = (float(coord["x"]), float(coord["y"]))
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dreamscape_memory_solve: skipping malformed map entry %r=%r",
                word,
                coord,
            )
    return out


def _resolve_taps(
    words: list[str],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
) -> tuple[list[tuple[str, Point]], list[str]]:
    """Split OCR'd words into (word, tap-point) hits and unmapped misses.

    Percentage coordinates are converted to device pixels the same way the DSL
    click step does: ``px = pct / 100 * device_dimension``.
    """
    hits: list[tuple[str, Point]] = []
    misses: list[str] = []
    for word in words:
        key = _normalize_word(word)
        if not key:
            continue
        coord = targets.get(key)
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
    hits, misses = _resolve_taps(words, targets, dev_w, dev_h)

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
            "dreamscape_memory_solve: %d unmapped word(s) — add to map.yaml: %s",
            len(misses),
            ", ".join(misses),
        )

    ctx.result.update({"words": words, "tapped": tapped, "unmapped": misses})


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = {
    "dreamscape_memory_solve": _exec_dreamscape_memory_solve,
}
