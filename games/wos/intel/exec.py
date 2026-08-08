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
from games.wos.intel import board_cache
from games.wos.intel import started as started_mem
from games.wos.intel.chain import (
    free_march_slots,
)
from games.wos.intel.chain import (
    queue_next_intel_run as _exec_queue_next_intel_run,
)
from games.wos.intel.detection import (
    DEPLOYLESS_KINDS,
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
    read_player_state_field,
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


async def _save_board_snapshot(
    ctx: DslExecContext,
    *,
    detected: int,
    fresh_count: int,
    tapped: bool,
) -> int:
    """Remember this pass's board state (returns the recorded ``viable_left``).

    TTL follows the «Refreshes in» timer the same run just OCR'd into player
    state, capped at 15 min (:mod:`board_cache`).
    """
    viable_left = board_cache.viable_left_after(fresh_count, tapped=tapped)
    refresh_raw = await read_player_state_field(ctx, "intel.refresh_in")
    try:
        refresh_s: float | None = float(refresh_raw) if refresh_raw else None
    except (TypeError, ValueError):
        refresh_s = None
    await board_cache.save_board(
        ctx.redis_client,
        ctx.player_id,
        detected=detected,
        viable_left=viable_left,
        refresh_in_s=refresh_s,
        now=time.time(),
    )
    return viable_left


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
    # Board-cache input: actionable pins BEFORE the slot filter — a fight pin
    # blocked only by "no free slot right now" still makes a later visit
    # worthwhile, so it must count toward the remembered board state.
    fresh_pre_slot_filter = len(fresh)

    # Camp pins (rescue/gather camps) dispatch WITHOUT taking a march-queue
    # slot — deploy pins (fight/skull/beast) need one. With every queue busy,
    # restrict the pick to slot-free kinds instead of skipping the run: camps
    # stay harvestable at any time (operator-confirmed game rule).
    slot_filter = ""
    if ctx.redis_client is not None and ctx.player_id:
        try:
            free_slots, _slot_detail = await free_march_slots(
                ctx.redis_client, ctx.player_id, now=time.time()
            )
        except Exception:
            logger.debug("tap_intel_fight: slot read failed", exc_info=True)
            free_slots = None
        if free_slots is not None and free_slots < 1:
            deployless = [m for m in fresh if m.kind in DEPLOYLESS_KINDS]
            if len(deployless) != len(fresh):
                slot_filter = "camp_only_no_slots"
            fresh = deployless

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
            action = "all_in_progress"
            reason = "no_deployless_pins" if slot_filter else "all_in_progress"
        else:
            action = "skipped"
            reason = plan_trace.get("reason")
        board_left = await _save_board_snapshot(
            ctx,
            detected=len(markers),
            fresh_count=fresh_pre_slot_filter,
            tapped=False,
        )
        ctx.result.update(
            {
                **plan_trace,
                "action": action,
                "reason": reason,
                "threshold": threshold,
                "detected": len(markers),
                "suppressed": len(suppressed),
                "started_active": len(started),
                "board_viable_left": board_left,
                **({"slot_filter": slot_filter} if slot_filter else {}),
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

    board_left = await _save_board_snapshot(
        ctx,
        detected=len(markers),
        fresh_count=fresh_pre_slot_filter,
        tapped=bool(tapped),
    )

    ctx.result.update(
        {
            "action": "tapped" if tapped else "tap_blocked",
            "board_viable_left": board_left,
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
    import cv2

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
            x0 = int(round(float(bbox["x"]) / 100.0 * w))
            y0 = int(round(float(bbox["y"]) / 100.0 * h))
            x1 = x0 + int(round(float(bbox["width"]) / 100.0 * w))
            y1 = y0 + int(round(float(bbox["height"]) / 100.0 * h))
            crop = image[y0:y1, x0:x1]
            # The top-right board counter («114») is tiny (~29px tall) on a
            # textured bar; at native size the OCR drops the leading digit
            # («114»→«14», live bs3 2026-08-08). Upscaling ≥3× (verified: 4×/5×
            # fast_digits read «114» cleanly, native reads «14») recovers it.
            # Sample a few scales and take the median parsed current so one bad
            # scale can't skew the budget.
            currents: list[int] = []
            for fx in (3.0, 4.0, 5.0):
                if crop.size == 0:
                    break
                src = cv2.resize(crop, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
                try:
                    res = await ocr.ocr_region(
                        src, Region(0, 0, src.shape[1], src.shape[0]), preprocess="fast_digits"
                    )
                except Exception:
                    logger.exception("intel stamina: ocr failed instance=%s", ctx.instance_id)
                    continue
                text = (getattr(res, "text", "") or "").strip()
                parsed = parse_stamina(text)
                if parsed is not None:
                    last_text = text
                    cur, parsed_max = parsed
                    currents.append(cur)
                    if parsed_max is not None:
                        maximum = parsed_max
            if currents:
                currents.sort()
                current = currents[(len(currents) - 1) // 2]  # median
                if maximum is not None:
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


# Squad Settings VS strip: own power left of the VS badge, enemy right of it.
# Percent bboxes verified on a live bs3 (RU) 7-digit matchup («1 394 861» vs
# «127 015», dumped 2026-08-08). The enemy box MUST start right of the big icy
# «VS» badge — an earlier 55%-wide box swallowed the badge's right edge as a
# spurious leading «4» («127 015» → «4127015»), which read as 4.1M and fled a
# 10× winning fight. The own box skips the left fist icon.
_POWER_OWN_BBOX = (17.5, 8.8, 28.0, 3.6)
_POWER_ENEMY_BBOX = (64.0, 8.8, 18.0, 3.6)
# Fight only when the enemy is at most this fraction of our power. Squad
# Settings shows raw power, which overweights walls of low-tier troops, so the
# default keeps a healthy margin.
_POWER_GATE_DEFAULT_RATIO = 0.8


def _consensus_power(reads: list[int]) -> int:
    """Pick the most trustworthy power from several OCR reads of one number.

    A dropped digit reads ~10× low, a duplicated digit ~10× high — both are
    length outliers. So group the reads by digit-length, take the modal length
    (ties broken toward the *smaller* magnitude — an over-read inserts a phantom
    digit more often than a drop deletes a real one on these stylised glyphs),
    and return the median value within that group.
    """
    clean = sorted(r for r in reads if r > 0)
    if not clean:
        return 0
    # Median value: robust when the reads straddle the truth (one drop reads
    # ~10× low, one duplicate ~10× high — the correct middle read wins). With an
    # even count, prefer the lower-middle (a drop is the more common artefact).
    return clean[(len(clean) - 1) // 2]


def decide_power_gate(own: int, enemy: int, *, max_ratio: float) -> str:
    """Pure gate decision: ``fight`` / ``flee`` / ``fight`` when unreadable.

    Unreadable (either power ≤ 0) defaults to ``fight`` — that preserves the
    pre-gate behaviour instead of silently starving intel on an OCR hiccup;
    the margin in ``max_ratio`` is the actual safety mechanism.
    """
    if own <= 0 or enemy <= 0:
        return "fight"
    return "fight" if enemy <= own * max_ratio else "flee"


async def _exec_intel_power_gate(ctx: DslExecContext) -> None:
    """Read the Squad Settings VS powers and decide fight vs retreat.

    Writes ``intel.power_gate`` (``fight``/``flee``) + the raw numbers to the
    player hash — the scenario gates the deploy steps on that field. On
    ``flee`` this exec also backs out of the squad screen (system back), so an
    autonomous run never feeds the army into an unwinnable pin (the failure
    mode: dead troops → hospital → hours of healing downtime).
    """
    import cv2

    from layout.types import Region
    from services import get_ocr_client

    max_ratio = float(ctx.args.get("max_ratio") or _POWER_GATE_DEFAULT_RATIO)
    actions = dsl_runtime.bot_actions()
    ocr = get_ocr_client()
    # DECISION-CRITICAL read → lossless adb screencap, not the scrcpy stream:
    # H.264 compression mangles the small power digits (live bs5: «784 720»
    # read as 7720 and «75 063» as 750635 off the stream; both read clean off
    # a PNG frame). One extra screencap per fight is worth a correct gate.
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr_adb, ctx.instance_id)
    except Exception:
        logger.debug("intel power gate: adb capture failed, falling back", exc_info=True)
        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        except Exception:
            logger.exception("intel power gate: capture failed instance=%s", ctx.instance_id)
            image = None

    own = enemy = 0
    own_reads: list[int] = []
    enemy_reads: list[int] = []
    if image is not None:
        h, w = image.shape[:2]

        async def read_power(bbox: tuple[float, float, float, float]) -> tuple[int, list[int]]:
            """OCR a VS-strip power number across scales, return (value, all_reads).

            The stylised 6-7 digit powers are small; at native size the OCR drops
            the thin trailing digit off a 7-digit number («1 394 861» → «139486»)
            and at a big upscale it *duplicates* a digit («…139466199»). Either
            is a 10× error that flips the gate, so no single read is trusted:
            sample several scales and let the caller pick a consensus. The raw
            reads are surfaced for offline diagnosis.
            """
            x, y, ww, hh = bbox
            x0, y0 = int(x / 100.0 * w), int(y / 100.0 * h)
            x1, y1 = int((x + ww) / 100.0 * w), int((y + hh) / 100.0 * h)
            crop = image[y0:y1, x0:x1]
            if crop.size == 0:
                return 0, []
            reads: list[int] = []
            for fx in (2.0, 3.0, 4.0):
                src = cv2.resize(crop, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
                try:
                    res = await ocr.ocr_region(
                        src, Region(0, 0, src.shape[1], src.shape[0]), preprocess="fast_digits"
                    )
                except Exception:
                    logger.exception(
                        "intel power gate: ocr failed instance=%s", ctx.instance_id
                    )
                    continue
                digits = "".join(c for c in (getattr(res, "text", "") or "") if c.isdigit())
                if digits:
                    reads.append(int(digits))
            return (_consensus_power(reads), reads)

        own, own_reads = await read_power(_POWER_OWN_BBOX)
        enemy, enemy_reads = await read_power(_POWER_ENEMY_BBOX)

    decision = decide_power_gate(own, enemy, max_ratio=max_ratio)

    if ctx.redis_client is not None and ctx.player_id:
        try:
            await ctx.redis_client.hset(
                f"wos:player:{ctx.player_id}:state",
                mapping={
                    "intel.power_gate": decision,
                    "intel.power_gate.own": str(own),
                    "intel.power_gate.enemy": str(enemy),
                    "intel.power_gate.own_reads": ",".join(str(r) for r in own_reads),
                    "intel.power_gate.enemy_reads": ",".join(str(r) for r in enemy_reads),
                    "intel.power_gate.at": str(time.time()),
                },
            )
        except Exception:
            logger.exception("intel power gate: hset failed player=%s", ctx.player_id)

    # The deploy steps are gated by ``cond: intel.power_gate == "fight"`` and
    # DSL conds evaluate against the SQLite state store (``_state_flat``), NOT
    # the Redis player hash — the Redis write above is dashboard/debug only.
    # Without this store write the fight branch never fires and the run
    # strands the device on squad_settings with a staged, unfired squad
    # (live bs4 2026-08-05). Decision only: the raw numbers stay in Redis —
    # nesting ``intel.power_gate.own`` under the string-valued
    # ``intel.power_gate`` would clash in the nested store.
    if ctx.player_id:
        try:
            from config.state_store import get_state_store

            store = get_state_store().get_or_create(str(ctx.player_id))
            store.update_from_flat({"intel.power_gate": decision})
        except Exception:
            logger.exception(
                "intel power gate: state store write failed player=%s", ctx.player_id
            )

    if decision == "flee":
        try:
            await asyncio.to_thread(actions.system_back, ctx.instance_id)
        except Exception:
            logger.exception("intel power gate: back-out failed instance=%s", ctx.instance_id)

    ctx.result.update(
        {"action": decision, "own_power": own, "enemy_power": enemy, "max_ratio": max_ratio}
    )
    logger.info(
        "dsl exec intel_power_gate: decision=%s own=%d enemy=%d ratio_cap=%.2f instance=%s",
        decision, own, enemy, max_ratio, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {
    "confirm_intel_march_lease": _exec_confirm_intel_march_lease,
    "queue_next_intel_run": _exec_queue_next_intel_run,
    "tap_intel_fight": _exec_tap_intel_fight,
    "read_intel_stamina": _exec_read_intel_stamina,
    "intel_power_gate": _exec_intel_power_gate,
}
