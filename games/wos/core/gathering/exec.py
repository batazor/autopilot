"""DSL exec handlers for world-map resource gathering.

`gather_pick_scarcest` reads the four resource stockpiles the scenario OCR'd off
the top bar (stored under ``gathering.rss.<resource>``), parses their abbreviated
amounts (``74.7M`` / ``2.24M`` / ``812K`` / ``1.2B`` / ``74,749,653``) into
comparable numbers, and writes the scarcest one to ``gathering.target`` so the
scenario can branch to the matching resource tab.

The parsing/selection logic is pure (``_parse_amount`` / ``_pick_scarcest``) so it
can be unit-tested without Redis — see ``tests/test_gathering_exec.py``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from config.state_store import get_state_store
from tasks.dsl_exec.context import (
    DslExecContext,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

# Gatherable resources, in a stable order (ties resolve to the earliest).
RESOURCES: tuple[str, ...] = ("meat", "wood", "coal", "iron")

_SUFFIX_MULTIPLIER = {"k": 1e3, "m": 1e6, "b": 1e9}
_AMOUNT_RE = re.compile(r"([0-9]*\.?[0-9]+)\s*([kmb]?)")


def _parse_amount(text: str | None) -> float | None:
    """Parse a top-bar resource amount into a number.

    Handles thousands separators (``74,749,653``) and the game's abbreviated
    forms (``74.7M``, ``812K``, ``1.2B``). Returns ``None`` when no number is
    found so the resource is simply skipped rather than treated as zero.
    """
    if text is None:
        return None
    cleaned = str(text).strip().lower().replace(",", "").replace(" ", "")
    match = _AMOUNT_RE.search(cleaned)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    if suffix in _SUFFIX_MULTIPLIER:
        value *= _SUFFIX_MULTIPLIER[suffix]
    return value


def _pick_scarcest(amounts: dict[str, float | None]) -> str | None:
    """Return the resource with the lowest parsed amount, or None if none parsed."""
    valid = {res: amt for res, amt in amounts.items() if amt is not None}
    if not valid:
        return None
    # min() on dict keys keyed by value; insertion order breaks ties toward the
    # earliest resource in RESOURCES.
    return min(valid, key=lambda res: valid[res])


async def _read_hash(redis_client: Any, key: str, field: str) -> str:
    from tasks.dsl_exec.context import _decode_redis_raw

    return _decode_redis_raw(await redis_client.hget(key, field))


def _resource_balances(raw_by_res: dict[str, str]) -> dict[str, int]:
    """Parse the OCR'd top-bar amounts into integer balances, skipping any that
    don't parse (so a misread leaves the prior value untouched). Pure."""
    out: dict[str, int] = {}
    for res, raw in raw_by_res.items():
        amt = _parse_amount(raw)
        if amt is not None:
            out[res] = int(amt)
    return out


async def _exec_record_resources(ctx: DslExecContext) -> None:
    """Persist the four resource balances the scenario OCR'd off the top bar.

    Reuses the ``gathering.rss.<res>`` breadcrumbs, writes ``resources.<res>``
    (int) to the durable player profile + the Redis hot mirror — the affordability
    input the build / resource planners need (``plan_next(..., resources=...)``,
    the resource-world allocator). Best-effort: a single misread is skipped, not
    written as zero.
    """
    if ctx.redis_client is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        ctx.result.update({"reason": "empty_player_id"})
        return

    redis_key = f"wos:player:{player_id}:state"
    raw = {
        res: await _read_hash(ctx.redis_client, redis_key, f"gathering.rss.{res}")
        for res in RESOURCES
    }
    balances = _resource_balances(raw)
    if not balances:
        ctx.result.update({"reason": "no_amounts"})
        return

    updates = {f"resources.{res}": value for res, value in balances.items()}
    try:
        get_state_store().get_or_create(player_id).update_from_flat(updates)
    except Exception:
        logger.exception("dsl exec record_resources: state persist failed player=%s", player_id)
        ctx.result.update({"reason": "state_persist_failed", "resources": balances})
        return
    try:
        await ctx.redis_client.hset(redis_key, mapping={k: str(v) for k, v in updates.items()})
    except Exception:
        logger.debug("dsl exec record_resources: redis mirror failed", exc_info=True)

    ctx.result.update({"action": "stored", "resources": balances})
    logger.info("dsl exec record_resources: player=%s resources=%s", player_id, balances)


async def _exec_gather_pick_scarcest(ctx: DslExecContext) -> None:
    if ctx.redis_client is None:
        logger.warning("dsl exec gather_pick_scarcest: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec gather_pick_scarcest: empty player_id")
        ctx.result.update({"reason": "empty_player_id"})
        return

    redis_key = f"wos:player:{player_id}:state"
    amounts: dict[str, float | None] = {}
    for res in RESOURCES:
        raw = await _read_hash(ctx.redis_client, redis_key, f"gathering.rss.{res}")
        amounts[res] = _parse_amount(raw)

    target = _pick_scarcest(amounts)
    if target is None:
        logger.warning("dsl exec gather_pick_scarcest: no parseable amounts player=%s", player_id)
        ctx.result.update({"reason": "no_amounts", "amounts": amounts})
        return

    try:
        get_state_store().get_or_create(player_id).update_from_flat({"gathering.target": target})
    except Exception:
        logger.exception("dsl exec gather_pick_scarcest: state persist failed player=%s", player_id)
        ctx.result.update({"reason": "state_persist_failed", "target": target})
        return

    ctx.result.update({"action": "stored", "target": target, "amounts": amounts})
    logger.info(
        "dsl exec gather_pick_scarcest: player=%s target=%s amounts=%s",
        player_id,
        target,
        amounts,
    )


DSL_EXEC_HANDLERS = {
    "gather_pick_scarcest": _exec_gather_pick_scarcest,
    "record_resources": _exec_record_resources,
}
