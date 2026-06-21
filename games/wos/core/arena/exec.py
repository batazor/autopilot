"""DSL ``exec:`` handlers for the Arena of Glory screens.

``arena_pick_and_open`` replaces the blind ``click: arena.fight_button.1`` in
``arena.fight``: it OCRs the visible challenge rows, applies the paid
exclude-own-alliance filter, and opens the chosen opponent's deploy screen —
fighting the first non-own row, refreshing the list when *every* row is our own
alliance, and stopping rather than ever attacking our own side.

**Gated OFF by default.** The filter only engages when the per-account toggle
``planner.arena.exclude_own_alliance`` is set. With it off the handler taps the
top opponent — identical to the old ``click`` — so wiring it into the live
scenario changes nothing until an operator opts in.

Pure decisions live in :func:`opponent_filter.plan_targets` (unit-tested); this
shell only does capture / OCR / tap. NOTE: the device-side flow (OCR accuracy on
live names, refresh cadence) still needs on-device (bs1) validation.
"""
from __future__ import annotations

import asyncio
import logging

from games.wos.core.arena.opponent_filter import (
    SETTING_KEY,
    plan_targets,
)

from config.paths import repo_root
from layout.area_manifest import load_area_doc
from layout.types import Point
from tasks import dsl_runtime
from tasks.dsl_exec.context import DslExecContext, _decode_redis_raw

logger = logging.getLogger(__name__)

_OPPONENT_REGIONS = tuple(f"arena.opponent.{i}" for i in range(1, 6))
_FIGHT_REGIONS = tuple(f"arena.fight_button.{i}" for i in range(1, 6))
_REFRESH_REGION = "arena.free_refresh"
_DEFAULT_MAX_REFRESH = 3
_REFRESH_SETTLE_S = 1.5


async def _current_screen(ctx: DslExecContext) -> str:
    if ctx.redis_client is None:
        return ""
    try:
        raw = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state", "current_screen"
        )
    except Exception:
        logger.debug("arena_pick: current_screen read failed", exc_info=True)
        return ""
    return _decode_redis_raw(raw)


async def _player_state_field(ctx: DslExecContext, field: str) -> str:
    if ctx.redis_client is None or not ctx.player_id or not field:
        return ""
    try:
        raw = await ctx.redis_client.hget(f"wos:player:{ctx.player_id}:state", field)
    except Exception:
        logger.debug("arena_pick: state read failed field=%s", field, exc_info=True)
        return ""
    return _decode_redis_raw(raw)


def _read_toggle_sync(player_id: str) -> bool:
    """Read the per-account toggle from the canonical store.

    ``reload()`` re-reads SQLite so a value set by the API (a *different*
    process) is picked up — the operator config never flows through Redis.
    """
    from config.state_store import get_state_store

    store = get_state_store()
    store.reload()
    gamer = store.get(player_id)
    return bool(gamer.get(SETTING_KEY, False)) if gamer is not None else False


async def _filter_enabled(ctx: DslExecContext) -> bool:
    """Per-account toggle (canonical store) — fail-closed on read error."""
    if not ctx.player_id:
        return False
    try:
        toggle = await asyncio.to_thread(_read_toggle_sync, ctx.player_id)
    except Exception:
        logger.debug("arena_pick: toggle read failed", exc_info=True)
        return False
    return bool(toggle)


async def _own_tags(ctx: DslExecContext) -> set[str]:
    """Alliance tags we treat as ours. Today: this account's own ``alliance.name``;
    the operator-level union across the fleet is a future enhancement."""
    name = (await _player_state_field(ctx, "alliance.name")).strip()
    return {name} if name else set()


async def _read_opponents(area_doc: dict, state_flat: dict, img) -> list[str]:  # noqa: ANN001
    """OCR the five opponent ``[TAG]Nickname`` labels (blank for empty rows)."""
    from analysis.overlay import evaluate_overlay_rules_async

    rules = [{"name": r, "region": r, "action": "text"} for r in _OPPONENT_REGIONS]
    rows = await evaluate_overlay_rules_async(
        img, area_doc, repo_root(), rules, state_flat=state_flat
    )
    return [str((rows.get(r) or {}).get("text") or "").strip() for r in _OPPONENT_REGIONS]


async def _tap_region(ctx: DslExecContext, actions, area_doc, state_flat, img, region) -> bool:  # noqa: ANN001
    """Tap the center of a named region (bbox percentages -> pixels)."""
    from layout.area_lookup import screen_region_by_name

    pair = screen_region_by_name(area_doc, region, state_flat=state_flat)
    if pair is None:
        logger.warning("arena_pick: region not found region=%s", region)
        return False
    bbox = pair[1].get("bbox")
    if not isinstance(bbox, dict):
        return False
    h, w = img.shape[:2]
    try:
        cx = (float(bbox["x"]) + float(bbox["width"]) / 2.0) / 100.0 * w
        cy = (float(bbox["y"]) + float(bbox["height"]) / 2.0) / 100.0 * h
    except (KeyError, TypeError, ValueError):
        return False
    try:
        return bool(
            await asyncio.to_thread(
                actions.tap,
                ctx.instance_id,
                Point(int(round(cx)), int(round(cy))),
                approval_region=region,
            )
        )
    except Exception:
        logger.exception("arena_pick: tap failed region=%s", region)
        return False


async def _exec_arena_pick_and_open(ctx: DslExecContext) -> None:
    args = ctx.args or {}
    try:
        max_refresh = max(0, int(args.get("max_refresh", _DEFAULT_MAX_REFRESH)))
    except (TypeError, ValueError):
        max_refresh = _DEFAULT_MAX_REFRESH

    actions = dsl_runtime.bot_actions()
    try:
        area_doc = load_area_doc(repo_root())
    except Exception:
        logger.exception("arena_pick: area manifest load failed")
        ctx.result.update({"action": "area_load_failed"})
        return
    state_flat = {"current_screen": await _current_screen(ctx)}

    enabled = await _filter_enabled(ctx)
    own = await _own_tags(ctx) if enabled else set()
    ctx.result.update({"enabled": enabled, "own_tags": sorted(own)})

    refreshes_left = max_refresh
    for _ in range(max_refresh + 1):
        img = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        if img is None or getattr(img, "size", 0) == 0:
            ctx.result.update({"action": "capture_failed"})
            return

        # Filter off -> base behaviour: open the top opponent, no OCR. Keeps the
        # live scenario byte-for-byte equivalent to the old direct click.
        if not enabled:
            await _tap_region(ctx, actions, area_doc, state_flat, img, _FIGHT_REGIONS[0])
            ctx.result.update({"action": "fight", "fight_region": _FIGHT_REGIONS[0]})
            return

        labels = await _read_opponents(area_doc, state_flat, img)
        plan = plan_targets(labels, own, enabled=True, can_refresh=refreshes_left > 0)
        ctx.result.update(
            {"plan": plan.action, "reason": plan.reason, "skipped": list(plan.skipped)}
        )

        if plan.action == "fight" and plan.fight_index is not None:
            region = _FIGHT_REGIONS[plan.fight_index]
            await _tap_region(ctx, actions, area_doc, state_flat, img, region)
            ctx.result.update(
                {"action": "fight", "fight_region": region, "fight_index": plan.fight_index}
            )
            return
        if plan.action == "refresh":
            await _tap_region(ctx, actions, area_doc, state_flat, img, _REFRESH_REGION)
            refreshes_left -= 1
            await asyncio.sleep(_REFRESH_SETTLE_S)
            continue
        # stop: tap nothing — the scenario's squad match then fails and the
        # fight loop ends, so we never attack our own alliance.
        ctx.result.update({"action": "stop"})
        return

    ctx.result.update({"action": "stop", "reason": "refreshes_exhausted"})


DSL_EXEC_HANDLERS = {
    "arena_pick_and_open": _exec_arena_pick_and_open,
}
