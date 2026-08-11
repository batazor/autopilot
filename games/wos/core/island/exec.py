"""DSL ``exec:`` handler: Daybreak Island state reader → ``planner['island']['owned']``.

Auto-discovered by ``config.module_exec_registry``. Un-blinds the island planner:
the preceding ``ocr:`` steps store each island stat to ``island.read.<stat>``; this
assembles them into the IslandState-shaped ``owned`` dict and persists it. Island's
``observed_input`` is the FLAT key ``island.tree_of_life.level`` (not ``island.owned``),
so it's mirrored via ``extra_flat`` so ``botctl planners`` clears blind. No-ops
cleanly until the island stat regions are labeled.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from games.wos.core.readers import collect_read_fields, persist_planner_owned
from games.wos.core.readers.parse import coerce_int

from tasks.dsl_exec.context import (
    DslExecContext,
    resolve_player_id_for_device_level_exec,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Scalar island stats read directly off the screen (furnace_level is read elsewhere
# from buildings.levels.furnace; the island planner pulls it from there).
_ISLAND_SCALARS = ("tree_of_life_level", "prosperity", "life_essence")


def parse_island_state(read_fields: Mapping[str, object]) -> dict[str, object]:
    """``island.read.*`` → IslandState-shaped dict.

    ``island.read.<scalar>`` → top-level int; ``island.read.decoration.<id>`` →
    ``decorations`` dict; ``island.read.lumber.<n>`` → ``lumber_camp_levels`` list
    (ordered by key). Pure + fixture-testable.
    """
    owned: dict[str, object] = {}
    decorations: dict[str, int] = {}
    lumber: dict[str, int] = {}
    for key, val in read_fields.items():
        if not key.startswith("island.read."):
            continue
        rest = key[len("island.read."):]
        n = coerce_int(val)
        if n is None:
            continue
        if rest in _ISLAND_SCALARS:
            owned[rest] = n
        elif rest.startswith("decoration."):
            decorations[rest.split(".", 1)[1]] = n
        elif rest.startswith("lumber."):
            lumber[rest.split(".", 1)[1]] = n
    if decorations:
        owned["decorations"] = decorations
    if lumber:
        owned["lumber_camp_levels"] = [lumber[k] for k in sorted(lumber)]
    return owned


async def _exec_sync_island_state(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_island_state: no redis client — skipping")
        return

    player_id = await resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec sync_island_state: empty player_id — skipping")
        return

    read_fields = await collect_read_fields(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id, prefix="island.read."
    )
    owned = parse_island_state(read_fields)
    level = owned.get("tree_of_life_level")
    if level is None:
        logger.info("dsl exec sync_island_state: no tree-of-life level read (labeled?) — skipping")
        return

    if not await persist_planner_owned(
        ctx.redis_client, player_id=player_id, instance_id=ctx.instance_id,
        domain="island", owned=owned,
        # island's observed_input is the flat key — mirror it so `blind` clears.
        extra_flat={"island.tree_of_life.level": level},
    ):
        return

    from dashboard.dashboard_events import publish_dashboard_event_throttled_async

    await publish_dashboard_event_throttled_async(
        ctx.redis_client, topic="player", player_id=player_id, reason="sync_island_state"
    )
    ctx.result.update({"action": "synced", "tree_of_life_level": level, "player_id": player_id})
    logger.info(
        "dsl exec sync_island_state: tree=%s player=%s instance=%s",
        level, player_id, ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"sync_island_state": _exec_sync_island_state}
