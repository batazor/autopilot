"""DSL ``exec:`` handler: hero-gear reader → ``planner['hero_gear']['owned']``.

Auto-discovered by ``config.module_exec_registry``. Un-blinds the hero_gear
planner: the preceding ``ocr:`` steps store each piece/track level to
``hero_gear.read.<piece_id>.<track>`` (6 pieces × 3 tracks: enhance/mastery/widget);
this collects them into ``{piece_id: {track: level}}`` and persists via the shared
durable-SQLite + Redis-mirror path. No-ops cleanly until the Hero Gear screen is
found and its 18 cells are labeled.
"""
from __future__ import annotations

import logging

from games.wos.core.readers import (
    collect_read_fields,
    parse_owned_nested,
    persist_planner_owned,
)

from tasks.dsl_exec.context import (
    DslExecContext,
    resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)


async def _exec_sync_hero_gear(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_hero_gear: no redis client — skipping")
        return

    player_id = await resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_hero_gear: empty player_id — skipping")
        return

    read_fields = await collect_read_fields(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id, prefix="hero_gear.read."
    )
    owned = parse_owned_nested(read_fields, domain="hero_gear")
    if not owned:
        logger.info("dsl exec sync_hero_gear: no pieces read (regions labeled?) — skipping")
        return

    if not await persist_planner_owned(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id,
        domain="hero_gear", owned=owned,
    ):
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client, topic="player", player_id=player_id, reason="sync_hero_gear"
    )
    ctx.result.update({"action": "synced", "pieces": len(owned), "player_id": player_id})
    logger.info(
        "dsl exec sync_hero_gear: pieces=%s player=%s instance=%s",
        len(owned), player_id, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"sync_hero_gear": _exec_sync_hero_gear}
