"""``exec: drain_use_all`` — spend whole item stacks in one tap.

Many WoS consume popups (VIP increase-level, speedups, resource chests, gear
materials, …) render each owned item as a green ``×N`` "use-all" pill next to a
per-item ``Use`` button. Tapping ``Use`` spends one item, so a stack of 99 means
99 taps; tapping the pill spends the whole stack at once.

This handler drains the currently-open popup: it taps every ``×N`` pill it can
find (one tap each, re-capturing between taps so a freshly-emptied row drops out
of the search), then falls back to per-item ``Use`` for rows that have no stack
pill. Both targets are global ``button.*`` regions resolved full-frame, so any
screen reuses it by labeling its own ``button.use_all`` / ``button.use`` crops
and dropping ``- exec: drain_use_all`` where the popup is open.

Args (all optional):
  use_all_region: region of the ×N use-all pill   (default ``button.use_all``)
  use_region:     per-item Use fallback            (default ``button.use``)
  max:            safety cap on taps per phase      (default 6)
  settle_ms:      pause after each tap before the next capture (default 1000)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.paths import repo_root
from layout.area_manifest import load_area_doc
from layout.types import Point
from tasks import dsl_runtime
from tasks.dsl_exec.context import DslExecContext, _decode_redis_raw

logger = logging.getLogger(__name__)

# Per-phase tap cap so a frame that keeps matching (stuck popup, undrainable
# stack) can't tap forever — bounded work, then the scenario moves on.
_DEFAULT_MAX_TAPS = 6
# Default settle so the count/row redraws before the next capture decides
# whether the stack is gone.
_DEFAULT_SETTLE_MS = 1000


async def _current_screen(ctx: DslExecContext) -> str:
    if ctx.redis_client is None:
        return ""
    try:
        raw = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state", "current_screen"
        )
    except Exception:
        logger.debug("drain_use_all: current_screen read failed", exc_info=True)
        return ""
    return _decode_redis_raw(raw)


async def _match_and_tap(
    ctx: DslExecContext,
    actions: Any,
    area_doc: dict[str, Any],
    region: str,
    state_flat: dict[str, Any],
) -> bool:
    """Capture, full-frame match ``region``, tap its center. ``False`` on miss."""
    from analysis.overlay import evaluate_overlay_rules_async
    from layout.area_lookup import screen_region_by_name

    img = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    if img is None or getattr(img, "size", 0) == 0:
        return False
    # Inherit the region's own match threshold from area.yaml — the bare rule
    # below would otherwise fall back to the engine default and admit weaker
    # (false-positive) matches than the region was tuned for.
    threshold = 0.9
    pair = screen_region_by_name(area_doc, region, state_flat=state_flat)
    if pair is not None:
        try:
            threshold = float(pair[1].get("threshold", 0.9))
        except (TypeError, ValueError):
            threshold = 0.9
    rule = {
        "name": f"drain_use_all.{region}",
        "region": region,
        "action": "exist",
        "threshold": threshold,
    }
    rows = await evaluate_overlay_rules_async(
        img, area_doc, repo_root(), [rule], state_flat=state_flat
    )
    row = rows.get(rule["name"])
    if not (isinstance(row, dict) and row.get("matched")):
        return False
    # ``isSearch`` regions report the matched-center as ``tap_match_*``; static
    # regions fall back to the bbox-derived ``tap_*``.
    xp = row.get("tap_match_x_pct")
    yp = row.get("tap_match_y_pct")
    if xp is None:
        xp = row.get("tap_x_pct")
    if yp is None:
        yp = row.get("tap_y_pct")
    if xp is None or yp is None:
        return False
    h, w = img.shape[:2]
    x = int(round(float(xp) / 100.0 * w))
    y = int(round(float(yp) / 100.0 * h))
    try:
        return bool(
            await asyncio.to_thread(
                actions.tap, ctx.instance_id, Point(x, y), approval_region=region
            )
        )
    except Exception:
        logger.exception(
            "drain_use_all: tap failed region=%s instance=%s", region, ctx.instance_id
        )
        return False


async def _exec_drain_use_all(ctx: DslExecContext) -> None:
    args = ctx.args or {}
    use_all_region = str(args.get("use_all_region") or "button.use_all").strip()
    use_region = str(args.get("use_region") or "button.use").strip()
    try:
        max_taps = max(1, int(args.get("max") or _DEFAULT_MAX_TAPS))
    except (TypeError, ValueError):
        max_taps = _DEFAULT_MAX_TAPS
    try:
        settle_s = max(0.0, float(args.get("settle_ms") or _DEFAULT_SETTLE_MS) / 1000.0)
    except (TypeError, ValueError):
        settle_s = _DEFAULT_SETTLE_MS / 1000.0

    actions = dsl_runtime.bot_actions()
    try:
        area_doc = load_area_doc(repo_root())
    except Exception:
        logger.exception("drain_use_all: area manifest load failed")
        ctx.result.update({"action": "area_load_failed"})
        return
    state_flat = {"current_screen": await _current_screen(ctx)}

    # Phase 1: spend whole stacks via the ×N pill (one tap drains a stack).
    use_all_taps = 0
    for _ in range(max_taps):
        if not await _match_and_tap(ctx, actions, area_doc, use_all_region, state_flat):
            break
        use_all_taps += 1
        if settle_s:
            await asyncio.sleep(settle_s)

    # Phase 2: per-item Use for rows with no stack pill.
    use_taps = 0
    for _ in range(max_taps):
        if not await _match_and_tap(ctx, actions, area_doc, use_region, state_flat):
            break
        use_taps += 1
        if settle_s:
            await asyncio.sleep(settle_s)

    ctx.result.update(
        {
            "action": "drained",
            "use_all_region": use_all_region,
            "use_region": use_region,
            "use_all_taps": use_all_taps,
            "use_taps": use_taps,
        }
    )
    logger.info(
        "drain_use_all: instance=%s use_all_taps=%d use_taps=%d",
        ctx.instance_id,
        use_all_taps,
        use_taps,
    )
