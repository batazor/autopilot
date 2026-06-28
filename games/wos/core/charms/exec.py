"""DSL ``exec:`` handler: chief charms reader → ``planner['charms']['owned']``.

Auto-discovered by ``config.module_exec_registry``. Un-blinds the charms planner:
the preceding ``ocr:`` steps store each slot's level to ``charms.read.<slot_id>``
(18 fixed slots: infantry_1..marksman_6); this collects them into ``{slot_id: level}``
and persists via the shared durable-SQLite + Redis-mirror path. No-ops cleanly
until the Chief Charms screen is reached and its slot regions are labeled.
"""
from __future__ import annotations

import logging

from games.wos.core.readers import (
    collect_read_fields,
    parse_owned_flat,
    persist_planner_owned,
)

from tasks.dsl_exec.context import (
    DslExecContext,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)


async def _exec_sync_charms(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_charms: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_charms: empty player_id — skipping")
        return

    read_fields = await collect_read_fields(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id, prefix="charms.read."
    )
    owned = parse_owned_flat(read_fields, domain="charms")
    if not owned:
        logger.info("dsl exec sync_charms: no slots read (regions labeled?) — skipping")
        return

    if not await persist_planner_owned(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id,
        domain="charms", owned=owned,
    ):
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client, topic="player", player_id=player_id, reason="sync_charms"
    )
    ctx.result.update({"action": "synced", "slots": len(owned), "player_id": player_id})
    logger.info(
        "dsl exec sync_charms: slots=%s player=%s instance=%s",
        len(owned), player_id, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"sync_charms": _exec_sync_charms}
