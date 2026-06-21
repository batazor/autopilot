"""``exec: sync_building_name`` / ``sync_furnace_level`` — parse OCR text into the state store."""
from __future__ import annotations

import logging
import time

from config.building_name_parser import parse_building_name_level_instance
from config.buildings import get_building_registry
from config.state_store import get_state_store
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

_BUILDING_TITLE_FIELDS = ("building.name", "building.title", "furnace.name")
_FURNACE_LEVEL_FIELDS = ("furnace.level", "buildings.furnace.level", "buildings.levels.furnace")


async def _exec_sync_building_name(ctx: DslExecContext) -> None:
    """Parse OCR'd ``building.name`` text and persist the current building level.

    Called from ``building.upgrade.yaml`` which is ``device_level: true``
    (tutorial-driven flow runs before identity is established). So the
    ``ctx.player_id`` may be empty here and we fall back to
    ``active_player`` via :func:`_resolve_player_id_for_device_level_exec`.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_building_name: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_building_name: empty player_id — skipping")
        return

    state_key = f"wos:player:{player_id}:state"
    text = ""
    text_source = ""
    text_field = ""
    for field in _BUILDING_TITLE_FIELDS:
        raw_text = await ctx.redis_client.hget(state_key, field)
        text = _decode_redis_raw(raw_text)
        if text:
            text_source = "player" if field == "building.name" else f"player:{field}"
            text_field = field
            break
    if not text:
        inst_key = f"wos:instance:{ctx.instance_id}:state"
        for field in _BUILDING_TITLE_FIELDS:
            raw_text = await ctx.redis_client.hget(inst_key, field)
            text = _decode_redis_raw(raw_text)
            if text:
                text_source = "instance" if field == "building.name" else f"instance:{field}"
                text_field = field
                break
    if not text:
        logger.warning(
            "dsl exec sync_building_name: missing building title OCR text fields=%s",
            _BUILDING_TITLE_FIELDS,
        )
        return

    parsed = parse_building_name_level_instance(text, get_building_registry().buildings)
    if parsed is None:
        logger.warning(
            "dsl exec sync_building_name: cannot parse %s=%r",
            text_field or "building title",
            text,
        )
        return

    building, level, building_instance_id = parsed
    now = time.time()
    level_field = f"buildings.levels.{building_instance_id}"
    prev_level_raw = await ctx.redis_client.hget(state_key, level_field)
    prev_level = _decode_redis_raw(prev_level_raw)
    mapping = {
        level_field: str(level),
        "building.name.parsed_id": building.id,
        "building.name.parsed_instance_id": building_instance_id,
        "building.name.parsed_name": building.name,
        "building.name.parsed_level": str(level),
        "building.name.parsed_at": str(now),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec sync_building_name: redis hset failed key=%s", state_key)
        return
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state",
            mapping={
                "current_screen": building.id,
                "building.name": text,
                "building.name.parsed_id": building.id,
                "building.name.parsed_instance_id": building_instance_id,
                "building.name.parsed_name": building.name,
                "building.name.parsed_level": str(level),
                "building.name.parsed_at": str(now),
            },
        )
    except Exception:
        logger.exception(
            "dsl exec sync_building_name: instance state hset failed instance=%s",
            ctx.instance_id,
        )

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_building_name",
    )

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(
            {
                level_field: level,
                "buildings.state.text": text,
            }
        )
    except Exception:
        logger.exception(
            "dsl exec sync_building_name: state persist failed player=%s",
            player_id,
        )

    changed = prev_level != str(level)
    logger.info(
        "dsl exec sync_building_name: %s player=%s instance=%s building=%s old=%s new=%s text=%r source=%s",
        "updated" if changed else "unchanged",
        player_id,
        ctx.instance_id,
        building_instance_id,
        prev_level or "?",
        level,
        text,
        text_source,
    )


async def _exec_sync_furnace_level(ctx: DslExecContext) -> None:
    """Persist OCR'd furnace level from the chief profile.

    ``who_i_am`` reads ``furnace.level`` after ``player.id`` has bound
    ``ctx.player_id``/``active_player``. Mirror the value into both historical
    keys because scheduler gates still read ``buildings.furnace.level`` while
    the build planner consumes ``buildings.levels.furnace``.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_furnace_level: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_furnace_level: empty player_id — skipping")
        return

    state_key = f"wos:player:{player_id}:state"
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    level_text = ""
    source = ""
    for key, source_key in ((state_key, "player"), (inst_key, "instance")):
        for field in _FURNACE_LEVEL_FIELDS:
            raw = await ctx.redis_client.hget(key, field)
            level_text = _decode_redis_raw(raw)
            if level_text:
                source = f"{source_key}:{field}"
                break
        if level_text:
            break

    try:
        level = int(level_text)
    except (TypeError, ValueError):
        logger.warning(
            "dsl exec sync_furnace_level: cannot parse furnace level %r source=%s",
            level_text,
            source or "?",
        )
        return

    if level < 0:
        logger.warning("dsl exec sync_furnace_level: negative level=%s — skipping", level)
        return

    now = time.time()
    mapping = {
        "furnace.level": str(level),
        "buildings.furnace.level": str(level),
        "buildings.levels.furnace": str(level),
        "furnace.level.synced_at": str(now),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
        await ctx.redis_client.hset(inst_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec sync_furnace_level: redis hset failed")
        return

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(
            {
                "buildings.furnace.level": level,
                "buildings.levels.furnace": level,
            }
        )
    except Exception:
        logger.exception(
            "dsl exec sync_furnace_level: state persist failed player=%s",
            player_id,
        )

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_furnace_level",
    )

    ctx.result.update({"action": "synced", "level": level, "player_id": player_id})
    logger.info(
        "dsl exec sync_furnace_level: level=%s player=%s instance=%s source=%s",
        level,
        player_id,
        ctx.instance_id,
        source or "?",
    )
