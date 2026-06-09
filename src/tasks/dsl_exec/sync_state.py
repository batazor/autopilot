"""``exec: sync_building_name`` / ``sync_hero_unit`` — parse OCR text into the state store."""
from __future__ import annotations

import logging
import re
import time

from config.buildings import BuildingDef, get_building_registry
from config.state_store import get_state_store
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

_BUILDING_NAME_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?:Lv\.?|Level)\s*\.?\s*(?P<level>\d+)\s*$",
    re.IGNORECASE,
)


def _normalise_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _building_by_ocr_name(name: str) -> BuildingDef | None:
    wanted = _normalise_lookup_text(name)
    if not wanted:
        return None
    for building in get_building_registry().buildings:
        if _normalise_lookup_text(building.name) == wanted:
            return building
    return None


def _parse_building_name_level(text: str) -> tuple[BuildingDef, int] | None:
    m = _BUILDING_NAME_RE.match(text or "")
    if not m:
        return None
    building = _building_by_ocr_name(m.group("name"))
    if building is None:
        return None
    try:
        level = int(m.group("level"))
    except ValueError:
        return None
    if level <= 0:
        return None
    return building, level


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
    raw_text = await ctx.redis_client.hget(state_key, "building.name")
    text = _decode_redis_raw(raw_text)
    text_source = "player"
    if not text:
        raw_text = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state",
            "building.name",
        )
        text = _decode_redis_raw(raw_text)
        text_source = "instance"
    if not text:
        logger.warning("dsl exec sync_building_name: missing building.name OCR text")
        return

    parsed = _parse_building_name_level(text)
    if parsed is None:
        logger.warning("dsl exec sync_building_name: cannot parse building.name=%r", text)
        return

    building, level = parsed
    now = time.time()
    level_field = f"buildings.levels.{building.id}"
    prev_level_raw = await ctx.redis_client.hget(state_key, level_field)
    prev_level = _decode_redis_raw(prev_level_raw)
    mapping = {
        level_field: str(level),
        "building.name.parsed_id": building.id,
        "building.name.parsed_name": building.name,
        "building.name.parsed_level": str(level),
        "building.name.parsed_at": str(now),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec sync_building_name: redis hset failed key=%s", state_key)
        return

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
        building.id,
        prev_level or "?",
        level,
        text,
        text_source,
    )


async def _exec_sync_hero_unit(ctx: DslExecContext) -> None:
    """Snapshot the currently-open hero card into the SQLite state store.

    Expects the surrounding scenario to have just OCR'd
    ``page.heroes.unit.name`` and ``page.heroes.unit.level`` via ``store:``,
    so the values are sitting in ``wos:player:<pid>:state``. The hero ID is
    a normalised name slug (e.g. ``"Bahiti"`` → ``"bahiti"``) and the snapshot
    overwrites ``heroes.entries.<id>`` — re-running on the same card just
    refreshes the level, no merge.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_hero_unit: no redis client — skipping")
        return

    # ``sync_hero_unit.yaml`` is NOT ``device_level: true`` so the identity
    # gate in ``DslScenarioExecuteMixin.execute`` guarantees a non-empty
    # ``ctx.player_id`` before we get here. The defensive check stays for
    # robustness — an empty pid here means the gate was bypassed and the
    # scenario should have been skipped upstream.
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning(
            "dsl exec sync_hero_unit: empty player_id — gate bypass? skipping"
        )
        return

    state_key = f"wos:player:{player_id}:state"
    raw_name = await ctx.redis_client.hget(state_key, "page.heroes.unit.name")
    name = _decode_redis_raw(raw_name)
    if not name:
        logger.warning(
            "dsl exec sync_hero_unit: missing page.heroes.unit.name in %s — "
            "did the surrounding scenario OCR it with store:?",
            state_key,
        )
        return

    raw_level = await ctx.redis_client.hget(state_key, "page.heroes.unit.level")
    level_text = _decode_redis_raw(raw_level)
    try:
        level = int(level_text)
    except (TypeError, ValueError):
        logger.warning(
            "dsl exec sync_hero_unit: cannot parse level %r for hero %r",
            level_text, name,
        )
        return

    hero_id = _normalise_lookup_text(name)
    if not hero_id:
        logger.warning("dsl exec sync_hero_unit: empty hero id from name=%r", name)
        return

    now = time.time()
    entry_path = f"heroes.entries.{hero_id}"
    snapshot = {
        "name": name,
        "level": level,
        "seen_at": now,
    }
    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat({entry_path: snapshot})
    except Exception:
        logger.exception(
            "dsl exec sync_hero_unit: state persist failed player=%s hero=%s",
            player_id, hero_id,
        )
        return

    logger.info(
        "dsl exec sync_hero_unit: hero=%s name=%r level=%d player=%s",
        hero_id, name, level, player_id,
    )
