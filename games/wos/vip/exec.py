"""DSL ``exec:`` handlers contributed by the VIP module.

Auto-discovered and merged into ``tasks.dsl_exec.DSL_EXEC_REGISTRY`` by
``config.module_exec_registry`` at registry build time.

- ``sync_vip_level`` — persist the OCR'd VIP level (``page.vip.level`` on the VIP
  screen) into ``vip.level`` so the VIP planner sees the real level instead of the
  schema default. Clone of the keystone ``sync_furnace_level`` reader.
"""
from __future__ import annotations

import logging
import time

from config.state_store import get_state_store
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

_VIP_LEVEL_FIELDS = ("vip.level",)


async def _exec_sync_vip_level(ctx: DslExecContext) -> None:
    """Persist the OCR'd VIP level from the VIP screen.

    The preceding ``ocr: page.vip.level`` step (``store: vip.level``) wrote the
    reading to the player/instance hash; read it back, coerce to int, and persist
    to Redis (player + instance hashes) and the durable state store so the VIP
    planner's ``vip.level`` input is live.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_vip_level: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_vip_level: empty player_id — skipping")
        return

    state_key = f"wos:player:{player_id}:state"
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    level_text = ""
    source = ""
    for key, source_key in ((state_key, "player"), (inst_key, "instance")):
        for field in _VIP_LEVEL_FIELDS:
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
            "dsl exec sync_vip_level: cannot parse vip level %r source=%s",
            level_text,
            source or "?",
        )
        return

    if level < 0:
        logger.warning("dsl exec sync_vip_level: negative level=%s — skipping", level)
        return

    now = time.time()
    mapping = {"vip.level": str(level), "vip.level.synced_at": str(now)}
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
        await ctx.redis_client.hset(inst_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec sync_vip_level: redis hset failed")
        return

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat({"vip.level": level})
    except Exception:
        logger.exception("dsl exec sync_vip_level: state persist failed player=%s", player_id)

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_vip_level",
    )

    ctx.result.update({"action": "synced", "level": level, "player_id": player_id})
    logger.info(
        "dsl exec sync_vip_level: level=%s player=%s instance=%s source=%s",
        level,
        player_id,
        ctx.instance_id,
        source or "?",
    )


DSL_EXEC_HANDLERS = {
    "sync_vip_level": _exec_sync_vip_level,
}
