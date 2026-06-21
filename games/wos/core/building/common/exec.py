"""DSL exec: record a building's current level from its on-screen title.

A building screen's header reads ``"<Name> Lv. N"`` (e.g. ``"Furnace Lv. 1"``,
``"Hunters' Hut Lv. 3"``). A reader scenario OCRs that title into the
instance-state ``dsl_last_ocr_*`` breadcrumbs; this exec parses the level out and
writes ``buildings.levels.<slug>`` to:

- the durable SQLite player profile (source of truth for the build planner), and
- the Redis instance-state hash (cheap hot-path mirror).

This is the building-level reader the value-greedy build planner needs as input.
The parse core (:func:`_parse_level`) is pure so it can be unit-tested.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# "Furnace Lv. 1", "Hunters' Hut Lv. 3", "Sawmill Lv 2" → (name, level)
_LEVEL_RE = re.compile(r"([A-Za-z'’][A-Za-z'’.\- ]*?)\s*lv\.?\s*(\d+)", re.IGNORECASE)
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _parse_level(title: str) -> tuple[str, int] | None:
    """``"Furnace Lv. 1"`` → ``("furnace", 1)``; ``None`` when no level is found."""
    m = _LEVEL_RE.search(title or "")
    if not m:
        return None
    slug = _NONWORD_RE.sub("_", m.group(1).lower()).strip("_")
    if not slug:
        return None
    return slug, int(m.group(2))


async def _exec_record_building_level(ctx: Any) -> None:
    from tasks.dsl_exec.context import (
        _decode_redis_raw,
        _resolve_player_id_for_device_level_exec,
    )

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    parsed = _parse_level(_decode_redis_raw(await r.hget(inst_key, "dsl_last_ocr_value")))
    if parsed is None:
        ctx.result.update({"reason": "no_level"})
        return
    slug, level = parsed
    field = f"buildings.levels.{slug}"

    player = (await _resolve_player_id_for_device_level_exec(ctx)) or ctx.instance_id
    # Durable profile — levels are monotonic, so never downgrade on a mis-read.
    try:
        from config.state_store import get_state_store

        store = get_state_store().get_or_create(str(player))
        current = int(store.to_flat_dict().get(field, 0) or 0)
        if level > current:
            store.update_from_flat({field: level})
    except Exception:
        logger.exception("record_building_level: durable write failed field=%s", field)
    # Redis instance-state mirror.
    try:
        await r.hset(inst_key, field, str(level))
    except Exception:
        logger.debug("record_building_level: redis mirror failed", exc_info=True)

    ctx.result.update({"action": "recorded", "building": slug, "level": level})
    logger.info(
        "record_building_level: %s=%d player=%s instance=%s", field, level, player, ctx.instance_id
    )


def _read_levels(state: dict) -> dict[str, int]:
    """Pull ``buildings.levels.<slug>`` ints out of an instance-state hash."""
    prefix = "buildings.levels."
    levels: dict[str, int] = {}
    for raw_k, raw_v in (state or {}).items():
        k = raw_k.decode() if isinstance(raw_k, bytes) else str(raw_k)
        if not k.startswith(prefix):
            continue
        v = raw_v.decode() if isinstance(raw_v, bytes) else str(raw_v)
        try:
            levels[k[len(prefix):]] = int(v)
        except (TypeError, ValueError):
            continue
    return levels


async def _exec_plan_next_building(ctx: Any) -> None:
    """Connect the build planner: read recorded levels → ``plan_next`` → store.

    Reads ``buildings.levels.*`` (populated by ``record_building_level`` /
    ``record_onboarding_build``), runs the furnace-first value planner, and
    writes its choice to instance state (``planner.next_building`` /
    ``planner.next_to_level`` / ``planner.reason`` / ``planner.affordable``) so
    the rest of the system (and the operator) can see what to build next. The
    recommendation is only as complete as the level coverage so far — missing
    buildings read as not-built until their screens are visited and OCR'd.
    """
    from games.wos.core.building.planner import load_graph, plan_next

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await r.hgetall(inst_key)
    except Exception:
        state = {}
    levels = _read_levels(state)

    plan = plan_next(load_graph(), levels)
    step = plan.step
    mapping = {"planner.reason": plan.reason}
    if step is not None:
        mapping["planner.next_building"] = step.building_id
        mapping["planner.next_to_level"] = str(step.to_level)
        mapping["planner.affordable"] = "1" if plan.affordable else "0"
    else:
        mapping["planner.next_building"] = ""
    try:
        await r.hset(inst_key, mapping=mapping)
    except Exception:
        logger.debug("plan_next_building: state write failed", exc_info=True)

    nxt = step.building_id if step is not None else None
    ctx.result.update({"action": "planned", "next": nxt, "reason": plan.reason, "levels": levels})
    logger.info(
        "plan_next_building: next=%s to=%s reason=%s affordable=%s levels=%s instance=%s",
        nxt,
        getattr(step, "to_level", None),
        plan.reason,
        plan.affordable if step is not None else None,
        levels,
        ctx.instance_id,
    )


def _resolve_target_name(building: str, nav_buildings: dict) -> str | None:
    """Resolve a planner building id (or display name) to the EXACT scanned-map
    name via the canonical building registry — avoids fuzzy mismatches like
    routing to ``shelter_1`` when the planner asked for ``shelter``. ``None`` when
    it can't be mapped (caller falls back to the navigator's own matcher).

    ``nav_buildings`` is ``Navigator.buildings`` (norm name → (canvas_px, display)).
    """
    try:
        from config.building_name_parser import building_by_ocr_name
        from config.buildings import get_building_registry

        registry = get_building_registry().buildings
    except Exception:
        return None
    # canonical building id → the display name that exists on the map
    canon: dict[str, str] = {}
    for value in nav_buildings.values():
        disp = value[1]
        bdef = building_by_ocr_name(disp, registry)
        if bdef is not None:
            canon.setdefault(bdef.id, disp)
    if building in canon:  # planner passed a canonical id
        return canon[building]
    bdef = building_by_ocr_name(building, registry)  # planner passed a display name
    if bdef is not None and bdef.id in canon:
        return canon[bdef.id]
    return None


async def _exec_navigate_to_building(ctx: Any) -> None:
    """Drive the camera to the planned building via the radar navigator.

    The missing link between the planner and the upgrade scenario: reads the
    target (``building:`` step arg, else ``planner.next_building``), loads the
    latest scanned city map, and swipes until that building is centred — so the
    on-screen upgrade flow can then run. Best-effort: no city scan, no serial or
    a building absent from the map all return a reason instead of raising.
    """
    import asyncio

    from config.loader import load_settings
    from modules.radar.config import runs_root
    from modules.radar.navigator import Navigator, latest_city_run
    from tasks.dsl_exec.context import _decode_redis_raw

    r = ctx.redis_client
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    building = str(ctx.args.get("building") or "").strip()
    if not building and r is not None:
        building = _decode_redis_raw(await r.hget(inst_key, "planner.next_building"))
    if not building:
        ctx.result.update({"reason": "no_building"})
        return

    run = latest_city_run(runs_root())
    if run is None:
        ctx.result.update({"reason": "no_city_map"})
        return

    settings = load_settings()
    serial = next(
        (i.bluestacks_window_title for i in settings.instances if i.instance_id == ctx.instance_id),
        None,
    )
    adb_bin = settings.worker.adb_executable or "adb"

    def _route() -> dict[str, Any]:
        from modules.radar.device import RadarDevice, pick_serial

        nav = Navigator.from_run(run)
        # Map the planner's canonical id (e.g. "hunters_hut") to the EXACT name
        # in the scanned map via the building registry — so we never fuzzily
        # route to the wrong building. Fall back to the navigator's own match.
        route_name = _resolve_target_name(building, nav.buildings) or building
        if nav.find(route_name) is None:
            return {"ok": False, "reason": "not_in_map", "have": nav.names()}
        device = RadarDevice(serial or pick_serial(adb_bin), adb_bin)

        def _dismiss_popups() -> None:
            # Lost the map fix mid-route: a modal may be covering it. Reuse the
            # game-wide popup detector to tap its safe close (X / claim / tap-to-
            # continue). Best-effort — never let it break navigation.
            try:
                import asyncio

                from popup.detector import PopupDetector
                from tasks import dsl_runtime
                from tasks.dsl_exec.dismiss_popup import _popup_tap_target

                det = PopupDetector(dsl_runtime.ocr_client())
                for _ in range(3):
                    state = asyncio.run(det.detect(device.capture()))
                    tgt = _popup_tap_target(state)
                    if tgt is None:
                        break
                    (px, py), _region = tgt
                    device.tap(px, py)
            except Exception:
                logger.debug("navigate_to_building: popup dismiss failed", exc_info=True)

        ok = nav.route_to(
            building,
            device.capture,
            lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, 450),
            on_lost=_dismiss_popups,
        )
        # Optional: once centred, tap the building open so an upgrade scenario
        # can take over (the building panel reads "<Name> Lv. N"). The tap lands
        # below screen centre — the name plate floats above the footprint.
        if ok and ctx.args.get("open"):
            from modules.radar.navigator import open_tap_point

            device.tap(*open_tap_point())
        return {"ok": ok, "reason": "centered" if ok else "incomplete"}

    try:
        res = await asyncio.to_thread(_route)
    except Exception as exc:  # device/ADB/scan failure must not crash the scenario
        logger.exception("navigate_to_building failed (instance %s)", ctx.instance_id)
        res = {"ok": False, "reason": f"error: {exc}"}

    if r is not None:
        try:
            await r.hset(
                inst_key,
                mapping={
                    "nav.building": building,
                    "nav.ok": "1" if res.get("ok") else "0",
                    "nav.reason": str(res.get("reason", "")),
                },
            )
        except Exception:
            logger.debug("navigate_to_building: state write failed", exc_info=True)
    ctx.result.update({"action": "navigate", "building": building, **res})
    logger.info(
        "navigate_to_building: building=%s ok=%s reason=%s instance=%s",
        building, res.get("ok"), res.get("reason"), ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {
    "record_building_level": _exec_record_building_level,
    "plan_next_building": _exec_plan_next_building,
    "navigate_to_building": _exec_navigate_to_building,
}
