"""``exec: record_new_hero`` — record a hero from the new-unlock celebration page.

The ``heroes.sr.new`` screen ("SSR/SR — NEW", "Tap anywhere to continue") pops up
the moment a new hero is obtained (recruit, event, gift). The surrounding
``read_new_hero_unlock`` scenario OCRs the hero name into the instance hash
(``store: heroes.sr.new.name`` / ``scope: instance`` — the scenario is
``device_level`` so it has no player binding for a player-scoped store). This
handler:

1. Reads that OCR'd name back, maps it to a canonical hero id (:mod:`hero_name_match`).
2. Resolves the instance's active player and merges the hero into
   ``heroes.entries.<id>`` with ``available: True`` (so the roster/allocator
   counts it, identical schema to ``scan_heroes_grid``), preserving any prior
   level/shard fields.
3. Re-projects ``heroes.roster`` so the resource allocator sees the new hero
   immediately (shared :func:`project_roster_to_redis`).
4. Clears the consumed name field so a stacked second unlock whose OCR fails
   cannot re-record this hero (the loop in the scenario re-reads per page).

Never raises and never blocks dismissal: if the name is unreadable, no hero
matches, or no active player is known, it logs and returns — the scenario still
taps the page away.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from games.wos.heroes.heroes.hero_name_match import match_hero_id
from games.wos.heroes.heroes.sync_hero_roster import project_roster_to_redis

from config.heroes import get_hero_registry
from config.state_store import get_state_store

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Redis instance-hash field the scenario's ``store: heroes.sr.new.name`` lands in.
NAME_FIELD = "heroes.sr.new.name"


def merge_new_hero_entry(
    prev: Any, name: str, *, now: float
) -> dict[str, Any]:
    """Merge an unlock into a ``heroes.entries`` value, preserving prior fields.

    A hero may have been scanned earlier as locked (with shard counts); unlocking
    only flips ``available`` and stamps the unlock — it must not drop the level /
    shard data ``scan_heroes_grid`` recorded. ``unlocked_at`` is set once.
    """
    entry: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}
    entry["name"] = name
    entry["available"] = True
    entry["seen_at"] = now
    entry.setdefault("unlocked_at", now)
    entry["source"] = "sr_new_unlock"
    return entry


async def _clear_name_field(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        return
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state", mapping={NAME_FIELD: ""}
        )
    except Exception:
        logger.debug("record_new_hero: failed to clear %s", NAME_FIELD, exc_info=True)


async def _exec_record_new_hero(ctx: DslExecContext) -> None:
    # Imported lazily: ``tasks.dsl_exec.context`` eagerly builds the exec registry
    # (which loads this module), so a module-level import here is a circular one.
    from tasks.dsl_exec.context import (
        _decode_redis_raw,
        _resolve_player_id_for_device_level_exec,
    )

    if ctx.redis_client is None:
        logger.warning("dsl exec record_new_hero: no redis client — skipping")
        return

    raw = await ctx.redis_client.hget(
        f"wos:instance:{ctx.instance_id}:state", NAME_FIELD
    )
    name_text = _decode_redis_raw(raw)
    if not name_text:
        logger.info(
            "dsl exec record_new_hero: no %s on instance %s — name unreadable, "
            "dismiss only",
            NAME_FIELD, ctx.instance_id,
        )
        return

    registry = get_hero_registry()
    hero_id, score = match_hero_id(name_text, registry)
    if not hero_id:
        logger.warning(
            "dsl exec record_new_hero: OCR name=%r matched no hero — dismiss only",
            name_text,
        )
        await _clear_name_field(ctx)
        return

    hero_def = registry.by_id(hero_id)
    canonical = hero_def.name if hero_def is not None else name_text

    # Consume the field now so a stacked next-page OCR that fails can't re-record
    # this same hero on the following loop iteration.
    await _clear_name_field(ctx)

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    ctx.result["hero_id"] = hero_id
    ctx.result["hero_name"] = canonical
    ctx.result["match_score"] = score
    if not player_id:
        logger.warning(
            "dsl exec record_new_hero: hero=%s name=%r but no active player — "
            "roster not updated (dismiss only)",
            hero_id, canonical,
        )
        ctx.result["player"] = ""
        return

    now = time.time()
    try:
        store = get_state_store().get_or_create(player_id)
        prev = (store.snapshot().heroes.entries or {}).get(hero_id)
        entry = merge_new_hero_entry(prev, canonical, now=now)
        store.update_from_flat({f"heroes.entries.{hero_id}": entry})
    except Exception:
        logger.exception(
            "dsl exec record_new_hero: persist failed player=%s hero=%s",
            player_id, hero_id,
        )
        return

    await project_roster_to_redis(ctx.redis_client, player_id, ctx.instance_id)

    ctx.result["player"] = player_id
    logger.info(
        "dsl exec record_new_hero: ADDED hero=%s name=%r player=%s score=%.3f "
        "(ocr=%r)",
        hero_id, canonical, player_id, score, name_text,
    )
