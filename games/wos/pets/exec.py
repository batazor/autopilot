"""DSL ``exec:`` handler: pet ownership reader → ``planner['pets']['owned']``.

Auto-discovered and merged into ``tasks.dsl_exec.DSL_EXEC_REGISTRY`` by
``config.module_exec_registry``. Un-blinds the pets planner: the preceding
``ocr:`` steps store each pet cell to ``pets.read.<pet_id>.<stat>``; this assembles
them into ``{pet_id: {level, refine, skill}}`` and persists via the shared
durable-SQLite + Redis-mirror path. No-ops cleanly until the Pet-Hall roster is
labeled (no read fields → nothing to write).
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


async def _exec_sync_pet_owned(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_pet_owned: no redis client — skipping")
        return

    player_id = await resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_pet_owned: empty player_id — skipping")
        return

    read_fields = await collect_read_fields(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id, prefix="pets.read."
    )
    owned = parse_owned_nested(read_fields, domain="pets", require="level")
    if not owned:
        logger.info("dsl exec sync_pet_owned: no pet rows read (regions labeled?) — skipping")
        return

    if not await persist_planner_owned(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id,
        domain="pets", owned=owned,
    ):
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client, topic="player", player_id=player_id, reason="sync_pet_owned"
    )
    ctx.result.update({"action": "synced", "pets": len(owned), "player_id": player_id})
    logger.info(
        "dsl exec sync_pet_owned: pets=%s player=%s instance=%s",
        len(owned), player_id, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"sync_pet_owned": _exec_sync_pet_owned}
