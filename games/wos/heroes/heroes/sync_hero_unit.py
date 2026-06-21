"""``exec: sync_hero_unit`` — snapshot the open hero card into the state store."""
from __future__ import annotations

import logging
import time

from config.building_name_parser import (
    normalise_building_lookup_text as _normalise_lookup_text,
)
from config.state_store import get_state_store
from tasks.dsl_exec.context import DslExecContext, _decode_redis_raw

logger = logging.getLogger(__name__)


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
