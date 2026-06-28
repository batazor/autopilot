"""``exec: scan_enemy_lineup`` — scout an opponent's arena defense into an enemy store.

Reached by Arena → tap an opponent in the ranking → their **Defensive Lineup** popup →
**Details** (the magnifier). That Details popup is identical to your own, so the same
portrait-id + gear/level reader (:func:`read_detail_rows`) identifies the opponent's heroes.
We write them — in top-to-bottom order, which is the defensive slot order — to the instance
state key ``arena.enemy_lineup`` as a JSON list ``[{slot, id, hero_class, level, gear}]``,
ready to pre-fill the arena optimizer's ENEMY side ("против кого").

NOTE: the popup shows ~3-4 of the 5 heroes at once; a follow-up swipe to read the bottom
rows is a refinement. Slots are the visible list order (an approximation of the on-board
front/back positions, which live in the formation view, not the Details list).
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from games.wos.heroes.heroes.scan_hero_details_list import read_detail_rows_scrolled

from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_ENEMY_STATE_KEY = "arena.enemy_lineup"


async def _exec_scan_enemy_lineup(ctx: DslExecContext) -> None:
    """Read the opponent Details popup (swiping for all 5) and store the enemy lineup."""
    actions = dsl_runtime.bot_actions()
    try:
        ordered = await read_detail_rows_scrolled(actions, ctx.instance_id, swipes=2)
    except Exception:
        logger.exception("scan_enemy_lineup: read failed instance=%s", ctx.instance_id)
        return

    if not ordered:
        logger.info("scan_enemy_lineup: no hero rows matched instance=%s", ctx.instance_id)
        return

    from games.wos.core.arena.optimizer import load_arena_catalog

    cat = load_arena_catalog()
    lineup = []
    for i, (hid, e) in enumerate(ordered):  # first-seen order = top-to-bottom slot order
        h = cat.get(hid)
        lineup.append({
            "slot": i + 1,
            "id": hid,
            "hero_class": h.hero_class if h else "",
            "level": e.get("level"),
            "gear": e.get("gear"),
            "match_score": e.get("match_score"),
        })

    if ctx.redis_client is None:
        logger.warning("scan_enemy_lineup: no redis — cannot persist instance=%s", ctx.instance_id)
        return
    payload = json.dumps({"scouted_at": time.time(), "heroes": lineup})
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state", _ENEMY_STATE_KEY, payload
        )
    except Exception:
        logger.exception("scan_enemy_lineup: persist failed instance=%s", ctx.instance_id)
        return

    logger.info(
        "scan_enemy_lineup: instance=%s enemy_heroes=%d (%s)",
        ctx.instance_id, len(lineup), ",".join(h["id"] for h in lineup),
    )
