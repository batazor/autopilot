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
from tasks.dsl_exec.context import DslExecContext, decode_redis_raw

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
    return decode_redis_raw(raw)


async def _player_state_field(ctx: DslExecContext, field: str) -> str:
    if ctx.redis_client is None or not ctx.player_id or not field:
        return ""
    try:
        raw = await ctx.redis_client.hget(f"wos:player:{ctx.player_id}:state", field)
    except Exception:
        logger.debug("arena_pick: state read failed field=%s", field, exc_info=True)
        return ""
    return decode_redis_raw(raw)


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

        # Filter off -> base behaviour: fight the BOTTOM opponent, no OCR. The
        # challenge list is ranked so the last seat is the lowest-ranked rival —
        # the operator's "weakest → best win odds" pick. Points are only gained by
        # attacking (never lost on defeat), so no strength read is needed.
        if not enabled:
            await _tap_region(ctx, actions, area_doc, state_flat, img, _FIGHT_REGIONS[-1])
            ctx.result.update({"action": "fight", "fight_region": _FIGHT_REGIONS[-1]})
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


# --- City → Arena navigation -------------------------------------------------
#
# Route: open the City-list panel → OCR-find the *Marksman* training row (the
# list is DYNAMIC — its y shifts with whatever else is active — so we locate it
# each run) → tap the row to jump the camera to the Marksman camp.
#
# The trailing "swipe half a screen left, then blind-tap the centre to open the
# Arena building" leg is DISABLED (operator decision): a fixed flick plus an
# unverified centre tap lands wherever the camera happens to be, so a miss opened
# whatever building sat under the tap instead of failing cleanly. The main-menu
# panel route above is what we keep for now. The `wait_screen: [arena]` gate in
# arena.fight.yaml catches the "camp reached but Arena never opened" case and
# aborts the run instead of letting the fight flow tap blind.
#
# Only the Marksman-row lookup needs vision; the rest are calibrated gestures.

# Both taps go through labelled regions rather than pixel literals — these used
# to be `Point(19, 550)` / `Point(116, 270)`, which are just these two regions'
# resolved centres on a 720×1280 frame, so the constants silently pinned the
# route to one resolution and drifted from the labelling they were copied from.
_PANEL_TOGGLE_REGION = "main_city.to.main_menu"   # City-list panel toggle
_PANEL_CITY_TAB_REGION = "main_menu.city"         # «Город» tab — the panel reopens on
                                       # whichever tab was active last (e.g. «Глушь»
                                       # march queues, seen live on bs3), and the
                                       # Marksman row only exists on the City tab.
_MARKSMAN_NAV_X_FRAC = 0.40            # tap the row card body → navigate to the camp
_PANEL_RESET_SWIPES = 3
_PANEL_FIND_SWEEPS = 6

# The Arena of Glory building sits directly to the RIGHT of the Marksman camp,
# so once the camera centres on the camp a swipe LEFT pans the view onto the
# Arena (the pan is inverted: drag left → content moves right). Then a blind tap
# on the centre opens it. The ``wait_screen: [arena]`` gate in arena.fight
# confirms the Arena opened (OCR «Арена») and aborts cleanly on a miss, so a
# mis-tap never runs the fight blind.
#
# We use a FIXED-lane ``swipe`` here, NOT ``swipe_direction`` — the latter
# randomises its start x (0.58–0.76 w) and clamps the end to the edge, so the
# actual pan distance varies run-to-run and the Arena never lands reliably at
# centre (this is why the original leg was pulled). Fixed start/end px make the
# pan deterministic. Tuned live on bs5 (wos_ru, 720×1280).
_ARENA_SWIPE_START_X = 620
_ARENA_SWIPE_END_X = 220
_ARENA_SWIPE_Y = 660
_ARENA_SWIPE_DURATION_MS = 400
# The fixed pan lands the Arena colosseum at ~0.65 w / 0.55 h (deterministic),
# so tap THERE, not the geometric screen centre — the camp is left, Arena right.
_ARENA_CENTER_X_FRAC = 0.65
_ARENA_CENTER_Y_FRAC = 0.55


async def _find_marksman_cy(actions, ocr, instance_id: str) -> tuple[int, int] | None:  # noqa: ANN001
    """Reset the City panel to the top, then sweep-scan for the Marksman training
    row. Returns its ``(centre_y, frame_width)`` or ``None`` if never found."""
    from games.wos.core.main_menu.exec import capture_panel_frame, scan_panel_rows

    for _ in range(_PANEL_RESET_SWIPES):
        await asyncio.to_thread(
            actions.swipe_direction, instance_id, direction="down", delta=500, duration_ms=350
        )
        await asyncio.sleep(0.4)
    for _ in range(_PANEL_FIND_SWEEPS):
        frame = await capture_panel_frame(actions, instance_id)
        if frame is None:
            return None
        rows = await scan_panel_rows(frame, ocr=ocr, with_status=False)
        row = next((r for r in rows if r.get("row") == "marksman"), None)
        if row is not None:
            return int(row["cy"]), int(frame.shape[1])
        await asyncio.to_thread(
            actions.swipe_direction, instance_id, direction="up", delta=400, duration_ms=350
        )
        await asyncio.sleep(0.5)
    return None


async def _exec_open_arena_via_city(ctx: DslExecContext) -> None:
    """Navigate main_city → the Marksman camp via the City-list panel.

    The final swipe-and-blind-tap that used to open the Arena building is
    disabled — see the module comment above.
    """
    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    inst = ctx.instance_id

    try:
        area_doc = load_area_doc(repo_root())
    except Exception:
        logger.exception("open_arena_via_city: area manifest load failed")
        ctx.fail("area_load_failed", action="area_load_failed")
        return
    state_flat = {"current_screen": await _current_screen(ctx)}
    img = await asyncio.to_thread(actions.capture_screen_bgr, inst)
    if img is None or getattr(img, "size", 0) == 0:
        ctx.fail("capture_failed", action="capture_failed")
        return

    # 1. Open the City-list panel.
    if not await _tap_region(ctx, actions, area_doc, state_flat, img, _PANEL_TOGGLE_REGION):
        logger.warning("open_arena_via_city: City-panel toggle tap rejected (inst=%s)", inst)
        ctx.fail("panel_not_opened", action="panel_not_opened")
        return
    await asyncio.sleep(1.3)

    # 1b. Force the «Город» tab — the panel remembers the last active tab.
    await _tap_region(ctx, actions, area_doc, state_flat, img, _PANEL_CITY_TAB_REGION)
    await asyncio.sleep(0.8)

    # 2. Locate the (dynamic) Marksman row by OCR.
    found = await _find_marksman_cy(actions, ocr, inst)
    if found is None:
        logger.warning(
            "open_arena_via_city: Marksman row not found after %d sweeps (inst=%s)",
            _PANEL_FIND_SWEEPS,
            inst,
        )
        ctx.fail("marksman_row_not_found", action="marksman_row_not_found")
        return
    cy, frame_w = found

    # 3. Tap the row → jump the camera to the Marksman camp.
    nav_x = int(_MARKSMAN_NAV_X_FRAC * frame_w)
    await asyncio.to_thread(
        actions.tap, inst, Point(nav_x, cy), approval_source="open_arena_via_city:marksman"
    )
    await asyncio.sleep(1.9)

    # 4. Pan onto the Arena (RIGHT of the camp → swipe LEFT) and open it with a
    #    blind centre tap. The arena.fight `wait_screen: [arena]` gate verifies
    #    the Arena screen (OCR «Арена») and aborts on a miss.
    dev_h, dev_w = img.shape[:2]
    await asyncio.to_thread(
        actions.swipe,
        inst,
        Point(_ARENA_SWIPE_START_X, _ARENA_SWIPE_Y),
        Point(_ARENA_SWIPE_END_X, _ARENA_SWIPE_Y),
        duration_ms=_ARENA_SWIPE_DURATION_MS,
    )
    await asyncio.sleep(1.2)
    await asyncio.to_thread(
        actions.tap,
        inst,
        Point(int(_ARENA_CENTER_X_FRAC * dev_w), int(_ARENA_CENTER_Y_FRAC * dev_h)),
        approval_source="open_arena_via_city:arena",
    )
    await asyncio.sleep(1.9)

    logger.info(
        "open_arena_via_city: camp via row cy=%d, swiped %d→%d → centre tap (inst=%s)",
        cy,
        _ARENA_SWIPE_START_X,
        _ARENA_SWIPE_END_X,
        inst,
    )
    ctx.result.update({"action": "arena_open_attempt", "marksman_cy": cy})


DSL_EXEC_HANDLERS = {
    "arena_pick_and_open": _exec_arena_pick_and_open,
    "open_arena_via_city": _exec_open_arena_via_city,
}
