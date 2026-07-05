"""Persist + self-heal the ``owned`` planner inputs read off-device.

Five investment planners (pets / charms / gear / hero_gear / island) are blind
because nothing reads their per-account ``owned`` state. Their input lives in the
free-form ``planner`` dict on ``GamerState`` (``config.state_schema`` — the
operator "annotation" layer), so the persist path differs from the furnace / vip
/ troop readers (which write *declared* schema fields):

* **Durable SQLite** must be written by **read-modify-write of the whole**
  ``planner`` **dict** via ``store.set("planner", merged)``. Do NOT use
  ``store.update_from_flat({"planner.<domain>.owned": ...})`` — it SILENTLY
  NO-OPS: ``GamerStateStore._set_nested`` stops recursing when an intermediate
  dict key (``planner["<domain>"]``) is missing, so the write looks successful
  but persists nothing. ``planner`` itself is a declared field, so
  ``store.set("planner", ...)`` reaches it.
* **Hot Redis mirror** carries the fact under the field name the planner's
  ``observed_inputs`` names (``<domain>.owned``) so ``botctl planners`` clears
  *blind*. island's observed_input is the flat ``island.tree_of_life.level``, so
  its reader also passes that exact key via ``extra_flat``.

Both go to the player AND instance hashes (the instance hash is the scheduler's
fast read and goes cold first after a flush; :func:`overlay_durable_planner_owned`
backfills it from SQLite).
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Domains whose ``owned`` input lives in the planner dict (not a schema field).
OWNED_DOMAINS: tuple[str, ...] = ("pets", "charms", "gear", "hero_gear", "island")


def _blob(owned: Mapping[str, Any]) -> str:
    """Compact, stable JSON for a hot-mirror field (sorted → diff-friendly)."""
    return json.dumps(owned, separators=(",", ":"), sort_keys=True)


async def collect_read_fields(
    redis: Any, *, player_id: str, instance_id: str, prefix: str
) -> dict[str, str]:
    """Merge ``<prefix>*`` fields the scenario's ``ocr:`` steps wrote to the hashes.

    The reader cron OCRs each labeled cell into a Redis field named
    ``<domain>.read.<entity>[.<stat>]`` (``store:`` on the ``ocr:`` step); this
    pulls them back from both the instance and player hashes (player wins) so the
    exec handler can assemble the ``owned`` dict via a pure parse function. Returns
    an empty dict when nothing was read (regions not labeled yet → reader no-ops).
    """
    out: dict[str, str] = {}
    if redis is None:
        return out
    # Instance first, then player, so a player-hash value wins on overlap.
    for key in (f"wos:instance:{instance_id}:state", f"wos:player:{player_id}:state"):
        try:
            raw = await redis.hgetall(key)
        except Exception:
            continue
        for k, v in (raw or {}).items():
            ks = k.decode() if isinstance(k, bytes) else str(k)
            if ks.startswith(prefix):
                out[ks] = v.decode() if isinstance(v, bytes) else str(v)
    return out


async def persist_planner_owned(
    redis: Any,
    *,
    player_id: str,
    instance_id: str,
    domain: str,
    owned: Mapping[str, Any],
    extra_flat: Mapping[str, Any] | None = None,
) -> bool:
    """Persist a planner ``owned`` input to Redis (hot) + durable SQLite.

    Returns ``True`` when persisted. Redis is written first (both player +
    instance hashes); a Redis failure aborts before the SQLite write (don't
    persist a half-state). A SQLite failure is logged but not fatal — the hot
    mirror is enough for the next planner tick, and the next cron re-persists.
    """
    if redis is None or not str(player_id).strip() or not isinstance(owned, dict):
        return False
    domain = str(domain).strip()
    if not domain:
        return False

    now = time.time()
    mapping: dict[str, str] = {
        f"{domain}.owned": _blob(owned),
        f"{domain}.owned.synced_at": str(now),
    }
    if extra_flat:
        # Each extra flat key is a planner observed_input (e.g. island's
        # island.tree_of_life.level) — stamp its own `.synced_at` too so the
        # freshness verdict can age it directly.
        for k, v in extra_flat.items():
            mapping[str(k)] = str(v)
            mapping[f"{k}.synced_at"] = str(now)

    try:
        await redis.hset(f"wos:player:{player_id}:state", mapping=mapping)
        await redis.hset(f"wos:instance:{instance_id}:state", mapping=mapping)
    except Exception:
        logger.exception("persist_planner_owned: redis hset failed domain=%s", domain)
        return False

    # Durable SQLite — read-modify-write the whole planner dict (see module docstring).
    try:
        from config.state_store import get_state_store

        store = get_state_store().get_or_create(player_id)
        planner = dict(getattr(store.snapshot(), "planner", None) or {})
        dom = dict(planner.get(domain) or {})
        dom["owned"] = dict(owned)
        # Sibling of "owned" (not inside it) so the *Body models ignore it but the
        # self-heal overlay can restore freshness after a Redis flush. pydantic
        # `extra=ignore` keeps CharmsBody(**dom) safe.
        dom["owned_synced_at"] = now
        planner[domain] = dom
        store.set("planner", planner)
    except Exception:
        logger.exception(
            "persist_planner_owned: sqlite persist failed domain=%s player=%s",
            domain,
            player_id,
        )

    return True


def overlay_durable_planner_owned(player_id: str, state: dict[str, Any]) -> None:
    """Self-heal ``<domain>.owned`` flat keys into a decoded ``state`` dict.

    The blind check and the calculators read ``<domain>.owned`` (and island's
    ``island.tree_of_life.level``) off the hot Redis mirror; after a flush that
    mirror is cold and the planners look blind despite live durable data. Fill any
    missing key from SQLite ``planner["<domain>"]["owned"]``. **Fill-only** — a
    live Redis reading always wins, so this can never staleness-overwrite. Mirrors
    :func:`games.wos.core.resources.adapter.overlay_durable_troops`.
    """
    missing = [d for d in OWNED_DOMAINS if f"{d}.owned" not in state]
    island_missing = "island.tree_of_life.level" not in state
    if not missing and not island_missing:
        return  # warm mirror — skip the SQLite read entirely

    try:
        from config.state_store import get_state_store

        store = get_state_store().get(str(player_id))
        if store is None:
            return
        planner = getattr(store.snapshot(), "planner", None) or {}
    except Exception:
        logger.debug(
            "overlay_durable_planner_owned: snapshot read failed player=%s",
            player_id,
            exc_info=True,
        )
        return

    for d in missing:
        dom = planner.get(d) or {}
        owned = dom.get("owned")
        if owned:
            state[f"{d}.owned"] = _blob(owned)
            sa = dom.get("owned_synced_at")
            if sa is not None and f"{d}.owned.synced_at" not in state:
                state[f"{d}.owned.synced_at"] = str(sa)
    if island_missing:
        island = planner.get("island") or {}
        owned = island.get("owned") or {}
        level = owned.get("tree_of_life_level")
        if level is not None:
            state["island.tree_of_life.level"] = str(level)
            sa = island.get("owned_synced_at")
            if sa is not None and "island.tree_of_life.level.synced_at" not in state:
                state["island.tree_of_life.level.synced_at"] = str(sa)
