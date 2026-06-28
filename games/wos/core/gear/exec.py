"""DSL ``exec:`` handler: chief gear reader → ``planner['gear']['owned']``.

Auto-discovered by ``config.module_exec_registry``. Un-blinds the gear planner:
the preceding ``ocr:`` steps store each piece's ordinal level (0-42, the
green_0→pink_t3_4 ladder) to ``gear.read.<piece_id>`` (6 fixed pieces); this
collects them into ``{piece_id: ordinal}`` and persists via the shared path.

NOTE: if labeling finds the screen shows a quality *badge* (e.g. "Pink T3-4")
rather than a number, add a label→ordinal decode against ``db/chief_gear.yaml``
in the scenario (or here) before persisting — the ladder order is the ordinal.
No-ops cleanly until the Chief Gear screen is reached and its regions are labeled.
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


async def _exec_sync_gear_owned(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_gear_owned: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_gear_owned: empty player_id — skipping")
        return

    read_fields = await collect_read_fields(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id, prefix="gear.read."
    )
    owned = parse_owned_flat(read_fields, domain="gear")
    if not owned:
        logger.info("dsl exec sync_gear_owned: no pieces read (regions labeled?) — skipping")
        return

    if not await persist_planner_owned(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id,
        domain="gear", owned=owned,
    ):
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client, topic="player", player_id=player_id, reason="sync_gear_owned"
    )
    ctx.result.update({"action": "synced", "pieces": len(owned), "player_id": player_id})
    logger.info(
        "dsl exec sync_gear_owned: pieces=%s player=%s instance=%s",
        len(owned), player_id, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"sync_gear_owned": _exec_sync_gear_owned}
