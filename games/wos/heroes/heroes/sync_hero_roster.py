"""``exec: sync_hero_roster`` — derive ``heroes.roster`` from the scanned grid.

``scan_heroes_grid`` already snapshots every visible hero into
``heroes.entries.<id>`` (owned/``available`` + level + shards). This handler is
the cheap projection on top of that data: it walks the owned entries, tags each
with its resource-allocator role from the static hero DB (``sub_class``), and
writes the ``heroes.roster`` JSON the resource/march allocator reads to un-blind
its ``heroes`` pool.

Contract consumed by ``games.wos.core.resources.adapter._parse_hero_roster``:
``heroes.roster`` = ``[{"id": "<id>", "role": "combat|gatherer", "free": true}, …]``.

Written to the Redis player hash (``wos:player:<id>:state`` — where the scheduler
reads player state) plus the instance-state mirror. Not persisted durably: it is
a pure projection of ``heroes.entries`` and is recomputed each scan.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from config.heroes import get_hero_registry
from config.state_store import get_state_store

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Static hero ``sub_class`` → resource-allocator role. Combat heroes staff the
# fight/rally actions (bear hunt, beast, rally); Growth heroes are the gatherers
# (``gather_resources``). Unknown/blank sub_class defaults to combat — the bulk
# of the roster, and the conservative choice (gather actions are lowest priority).
_SUBCLASS_ROLE: dict[str, str] = {"combat": "combat", "growth": "gatherer"}
_DEFAULT_ROLE = "combat"


def build_roster(
    entries: dict[str, Any],
    role_of: dict[str, str],
) -> list[dict[str, Any]]:
    """Pure: owned ``heroes.entries`` + a hero→role map → the roster list.

    ``role_of`` maps ``hero_id`` → ``"combat"``/``"gatherer"`` (lower-case). Only
    ``available`` (owned) heroes are included; every owned hero is ``free`` — the
    allocator subtracts the heroes already held by the in-flight ledger itself.
    Sorted (role, id) for a stable, diff-friendly payload.
    """
    roster: list[dict[str, Any]] = []
    for hid, entry in entries.items():
        if not isinstance(entry, dict) or not entry.get("available"):
            continue
        role = role_of.get(hid, _DEFAULT_ROLE)
        roster.append({"id": hid, "role": role, "free": True})
    roster.sort(key=lambda h: (h["role"], h["id"]))
    return roster


async def _exec_sync_hero_roster(ctx: DslExecContext) -> None:
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning("dsl exec sync_hero_roster: empty player_id — skipping")
        return

    try:
        snap = get_state_store().get_or_create(player_id).snapshot()
    except Exception:
        logger.exception("dsl exec sync_hero_roster: state read failed player=%s", player_id)
        return
    entries = dict(snap.heroes.entries) if snap.heroes.entries else {}
    if not entries:
        logger.info(
            "dsl exec sync_hero_roster: no scanned heroes yet player=%s "
            "(run scan_heroes_grid first)",
            player_id,
        )
        return

    registry = get_hero_registry()
    role_of: dict[str, str] = {}
    for hid in entries:
        hero_def = registry.by_id(hid)
        sub = (getattr(hero_def, "sub_class", "") or "").strip().lower()
        role_of[hid] = _SUBCLASS_ROLE.get(sub, _DEFAULT_ROLE)

    roster = build_roster(entries, role_of)
    payload = json.dumps(roster, separators=(",", ":"))

    if ctx.redis_client is not None:
        mapping = {"heroes.roster": payload, "heroes.roster.synced_at": str(time.time())}
        try:
            await ctx.redis_client.hset(f"wos:player:{player_id}:state", mapping=mapping)
            await ctx.redis_client.hset(f"wos:instance:{ctx.instance_id}:state", mapping=mapping)
        except Exception:
            logger.exception("dsl exec sync_hero_roster: redis write failed player=%s", player_id)
            return

    by_role: dict[str, int] = {}
    for h in roster:
        by_role[h["role"]] = by_role.get(h["role"], 0) + 1
    logger.info(
        "dsl exec sync_hero_roster: player=%s owned=%d roles=%s",
        player_id, len(roster), by_role,
    )
