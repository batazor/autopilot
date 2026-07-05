"""DSL ``exec:`` handlers for the Intel screen.

Thin I/O layer over three cohesive modules:

* :mod:`detection` — find the board pins and pick which to clear (cv2 + planner),
* :mod:`started`   — short-lived memory of pins already started, so a later run
  skips in-flight events and starts the next one on a free march,
* :mod:`march_lease` — confirm the march-slot lease from the deploy-screen TTL.

The handlers themselves only do Redis reads/writes, screen capture, and the tap.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

# NOTE: absolute imports only. This module is loaded standalone via
# ``importlib.spec_from_file_location`` (by the exec registry and the tests), so
# it has no parent package and relative imports would fail. The submodules it
# pulls in are imported as proper ``games.wos.intel.*`` package modules.
from games.wos.intel import started as started_mem
from games.wos.intel.detection import (
    IntelMarker,
    _pick_marker,
    detect_fight_markers,
    detect_intel_markers,
    select_planned_marker,
)
from games.wos.intel.march_lease import (
    confirm_intel_march_lease as _exec_confirm_intel_march_lease,
)
from games.wos.intel.march_lease import (
    parse_march_ttl_seconds,
)
from games.wos.intel.planner import DEFAULT_COST_PER_EVENT
from games.wos.intel.started import STARTED_MATCH_RADIUS_PX, STARTED_TTL_SECONDS
from games.wos.intel.state import (
    as_bool_arg,
    as_float_arg,
    as_int_arg,
    as_quota_arg,
    intel_reserve,
    parse_stamina,
    read_player_stamina,
)

from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.72
_DEFAULT_NMS_DISTANCE_PX = 40

__all__ = [
    "DSL_EXEC_HANDLERS",
    "IntelMarker",
    "_pick_marker",
    "detect_fight_markers",
    "detect_intel_markers",
    "parse_march_ttl_seconds",
    "select_planned_marker",
]


async def _exec_tap_intel_fight(ctx: DslExecContext) -> None:
    """Tap the most valuable affordable Intel marker, or skip the run.

    Selection runs through the value-greedy Intel planner (the "brain"): it ranks
    visible markers by loot value and spends ``stamina - reserve`` on the best
    one, declining when the run isn't worth it. Without a live stamina estimate it
    falls back to the deterministic colour/kind pick (previous behaviour).

    Pins the bot already started this window are filtered out first (see
    :mod:`started`): their march is still out, so re-picking them would burn the
    run. Skipping them lets this pass start the *next* best event on a free march.

    Args:
      threshold: grayscale template score floor, default 0.72.
      nms_distance_px: merge nearby duplicate matches, default 40.
      strategy: best_score | center | topmost | bottommost — the no-stamina
        fallback pick, default best_score.
      reserve: stamina to hold back for higher-priority demands (e.g. Joe), default 0.
      cost: stamina per marker, default 10 (mirrors budget.yaml intel_events).
      daily_quota_left: remaining intel runs today; omit for unlimited.
      min_value / priority_only: drop low-value / non-gold-purple markers.
      track_started: remember the tapped pin so the next run skips it, default true.
      started_ttl: seconds a started pin stays suppressed, default 600 (~1 round trip).
      started_radius_px: px radius matching a pin to a started coordinate, default 40.
    """
    threshold = as_float_arg(ctx.args, "threshold", _DEFAULT_THRESHOLD)
    nms_distance_px = as_int_arg(ctx.args, "nms_distance_px", _DEFAULT_NMS_DISTANCE_PX)
    strategy = str(ctx.args.get("strategy") or "best_score")
    track_started = as_bool_arg(ctx.args, "track_started", default=True)
    started_ttl = as_int_arg(ctx.args, "started_ttl", STARTED_TTL_SECONDS)
    started_radius = as_int_arg(ctx.args, "started_radius_px", STARTED_MATCH_RADIUS_PX)

    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec tap_intel_fight: capture_screen_bgr failed instance=%s",
            ctx.instance_id,
        )
        ctx.result.update({"action": "capture_failed"})
        return

    markers = detect_intel_markers(
        image,
        threshold=threshold,
        nms_distance_px=nms_distance_px,
    )

    # Drop pins whose march we already started this window — they're in flight,
    # so re-picking them wastes the run. What's left is the next best target.
    started = (
        await started_mem.load_started(
            ctx.redis_client, ctx.player_id, now=time.time(), ttl=started_ttl
        )
        if track_started
        else []
    )
    fresh, suppressed = started_mem.partition_markers(
        markers, started, radius=started_radius
    )

    stamina = await read_player_stamina(ctx)
    explicit_reserve = ctx.args.get("reserve")
    reserve = (
        as_int_arg(ctx.args, "reserve", 0)
        if explicit_reserve is not None
        else await intel_reserve(ctx)
    )
    marker, plan_trace = select_planned_marker(
        fresh,
        stamina=stamina,
        reserve=reserve,
        cost=as_int_arg(ctx.args, "cost", DEFAULT_COST_PER_EVENT),
        daily_quota_left=as_quota_arg(ctx.args, "daily_quota_left"),
        min_value=as_float_arg(ctx.args, "min_value", 0.0),
        priority_only=as_bool_arg(ctx.args, "priority_only"),
        fallback_strategy=strategy,
    )
    if marker is None:
        # Nothing actionable: nothing detected, every pin is already in flight, or
        # the planner declined the budget. Skip rather than clear a low-value pin
        # or overspend the shared stamina pool.
        if not markers:
            action = reason = "not_found"
        elif not fresh:
            action = reason = "all_in_progress"
        else:
            action = "skipped"
            reason = plan_trace.get("reason")
        ctx.result.update(
            {
                **plan_trace,
                "action": action,
                "reason": reason,
                "threshold": threshold,
                "detected": len(markers),
                "suppressed": len(suppressed),
                "started_active": len(started),
            }
        )
        logger.info(
            "dsl exec tap_intel_fight: action=%s instance=%s reason=%s "
            "stamina=%s detected=%d suppressed=%d",
            action,
            ctx.instance_id,
            reason,
            stamina,
            len(markers),
            len(suppressed),
        )
        return

    point = marker.center
    try:
        tapped = await asyncio.to_thread(
            actions.tap,
            ctx.instance_id,
            point,
            approval_region="intel.fight",
            approval_context={
                "score": round(marker.score, 4),
                "strategy": strategy,
                "kind": marker.kind,
                "color": marker.color,
            },
        )
    except Exception:
        logger.exception(
            "dsl exec tap_intel_fight: tap failed instance=%s point=%s",
            ctx.instance_id,
            point,
        )
        ctx.result.update({"action": "tap_failed", "tap_x": point.x, "tap_y": point.y})
        return

    # Remember the pin we just committed to so the next run skips it while its
    # march is out (only on a real tap — an approval-gated tap hasn't started it).
    recorded = False
    if tapped and track_started:
        recorded = await started_mem.record_started(
            ctx.redis_client,
            ctx.player_id,
            point.x,
            point.y,
            now=time.time(),
            ttl=started_ttl,
        )

    ctx.result.update(
        {
            "action": "tapped" if tapped else "tap_blocked",
            "tap_x": point.x,
            "tap_y": point.y,
            "score": marker.score,
            "kind": marker.kind,
            "color": marker.color,
            "stamina": stamina,
            "reserve": plan_trace.get("reserve"),
            "reason": plan_trace.get("reason"),
            "value": plan_trace.get("value"),
            "rank": plan_trace.get("rank"),
            "detected": len(markers),
            "suppressed": len(suppressed),
            "started_active": len(started),
            "started_recorded": recorded,
            "markers": [
                {
                    "kind": m.kind,
                    "color": m.color,
                    "x": m.x,
                    "y": m.y,
                    "w": m.w,
                    "h": m.h,
                    "score": m.score,
                }
                for m in markers[:20]
            ],
        }
    )
    logger.info(
        "dsl exec tap_intel_fight: action=%s instance=%s kind=%s tap=(%d,%d) "
        "score=%.3f detected=%d suppressed=%d",
        "tapped" if tapped else "tap_blocked",
        ctx.instance_id,
        marker.kind,
        point.x,
        point.y,
        marker.score,
        len(markers),
        len(suppressed),
    )


_STAMINA_READ_ATTEMPTS = 3
_STAMINA_RETRY_DELAY_S = 0.15


async def _exec_read_intel_stamina(ctx: DslExecContext) -> None:
    """Read the intel board's «current/max» stamina («43/70», green bar bottom-left)
    and store current (+ max) to player state for the stamina budget.

    The avatar bar reader (``read_stamina_bar``) doesn't populate on the RU build,
    so intel reads its own on-board counter — fresh every run, so ``tap_intel_fight``
    sees a real number rather than ``no_stamina_signal``.

    scrcpy H.264 compression frequently corrupts the small *denominator* on live
    frames ("70"→"710"/"10") while the numerator stays clean. So we:

    * retry the OCR on fresh frames (``stamina_read_attempts``, default 3) and take
      the first read whose max is plausible (``current <= max <= cap``, see
      :func:`state.parse_stamina`);
    * if no clean max appears, store the (reliable) current alone and KEEP the last
      known max instead of overwriting it with an OCR artefact.
    """
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc
    from layout.types import Region
    from services import get_active_module_catalog, get_ocr_client, get_repo_root

    area_doc = load_area_doc(get_repo_root(), game=get_active_module_catalog())
    pair = screen_region_by_name(area_doc, "intel.stamina") if area_doc else None
    bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
    if not isinstance(bbox, dict):
        ctx.result.update({"action": "unknown_region"})
        return

    actions = dsl_runtime.bot_actions()
    ocr = get_ocr_client()
    attempts = as_int_arg(ctx.args, "stamina_read_attempts", _STAMINA_READ_ATTEMPTS)

    captured_any = False
    last_text = ""
    current: int | None = None     # latest reliable current reading
    maximum: int | None = None     # plausible max, only if a clean read yielded one
    for attempt in range(attempts):
        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        except Exception:
            logger.exception("intel stamina: capture failed instance=%s", ctx.instance_id)
            image = None
        if image is not None:
            captured_any = True
            h, w = image.shape[:2]
            reg = Region(
                int(round(float(bbox["x"]) / 100.0 * w)),
                int(round(float(bbox["y"]) / 100.0 * h)),
                int(round(float(bbox["width"]) / 100.0 * w)),
                int(round(float(bbox["height"]) / 100.0 * h)),
            )
            try:
                res = await ocr.ocr_region(image, reg, preprocess="fast_line")
            except Exception:
                logger.exception("intel stamina: ocr failed instance=%s", ctx.instance_id)
                res = None
            if res is not None:
                last_text = (getattr(res, "text", "") or "").strip()
                parsed = parse_stamina(last_text)
                if parsed is not None:
                    current, parsed_max = parsed
                    if parsed_max is not None:
                        maximum = parsed_max
                        break  # fully plausible read — stop retrying
        if attempt < attempts - 1:
            await asyncio.sleep(_STAMINA_RETRY_DELAY_S)

    if current is None:
        action = "parse_failed" if captured_any else "capture_failed"
        ctx.result.update({"action": action, "text": last_text})
        logger.info(
            "dsl exec read_intel_stamina: action=%s instance=%s text=%r",
            action, ctx.instance_id, last_text,
        )
        return

    # Always persist current (reliable). Only persist max when a clean read gave
    # one — otherwise leave the field untouched so the last good max survives.
    mapping = {
        "stamina": str(current),
        "stamina_at": str(time.time()),
        "stamina_source": "intel",
    }
    if maximum is not None:
        mapping["stamina_max"] = str(maximum)
    if ctx.redis_client is not None and ctx.player_id:
        try:
            await ctx.redis_client.hset(
                f"wos:player:{ctx.player_id}:state", mapping=mapping
            )
        except Exception:
            logger.exception("intel stamina: hset failed player=%s", ctx.player_id)
    ctx.result.update(
        {
            "action": "measured",
            "stamina": current,
            "stamina_max": maximum,
            "max_stable": maximum is not None,
            "text": last_text,
        }
    )
    logger.info(
        "dsl exec read_intel_stamina: instance=%s stamina=%d max=%s stable=%s text=%r",
        ctx.instance_id, current, maximum, maximum is not None, last_text,
    )


DSL_EXEC_HANDLERS = {
    "confirm_intel_march_lease": _exec_confirm_intel_march_lease,
    "tap_intel_fight": _exec_tap_intel_fight,
    "read_intel_stamina": _exec_read_intel_stamina,
}
