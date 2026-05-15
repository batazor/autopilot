"""Dynamic edge resolver: ``main_city → event.X`` via Redis-stored block map.

Reads ``wos:instance:<id>:event_blocks`` (populated by the
``scan_event_blocks`` DSL exec handler) and returns the tap region for the
block currently hosting the requested event. Registers itself on import.

Minimal C semantics:
* hash empty / target absent → enqueues a fresh ``scan_event_blocks`` for the
  next overlay tick (best-effort, fire-and-forget) and returns ``None`` so the
  caller can fail this attempt and retry later;
* hash present and target found → returns ``[main_city.event.block.<N>]``.
"""
from __future__ import annotations

import logging
from typing import Any

from navigation.screen_graph import (
    DynamicEdgeSpec,
    Tap,
    register_edge_resolver,
)

logger = logging.getLogger(__name__)

_EVENT_BLOCKS_HASH_FMT = "wos:instance:{instance_id}:event_blocks"
_SCAN_RETRIGGER_KEY_FMT = "wos:instance:{instance_id}:event_blocks_scan_pending"
_SCAN_RETRIGGER_TTL_SECONDS = 60
"""Soft mutex so a missed-target route doesn't spam scan re-triggers."""


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw or "").strip()


async def _request_scan_refresh(redis_client: Any, instance_id: str) -> None:
    """Best-effort hint that the block map is stale. Idempotent via a 60s key
    so a tight retry loop doesn't enqueue dozens of scans."""
    if redis_client is None or not instance_id:
        return
    mutex = _SCAN_RETRIGGER_KEY_FMT.format(instance_id=instance_id)
    try:
        # SET NX EX — claim a soft lock for 60s.
        acquired = await redis_client.set(
            mutex, "1", ex=_SCAN_RETRIGGER_TTL_SECONDS, nx=True
        )
    except Exception:
        logger.debug(
            "event_blocks resolver: scan-mutex set failed instance=%s",
            instance_id,
            exc_info=True,
        )
        return
    if not acquired:
        return
    logger.info(
        "event_blocks resolver: requesting scan refresh instance=%s", instance_id
    )
    # The actual scan is pushed by the overlay rule `main_city.event_blocks.scan`
    # on the next main_city tick; the mutex above just avoids hammering. No
    # direct enqueue here so we stay decoupled from the scenario layer.


async def resolve_event_block(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    target = str(spec.get("target") or "").strip()
    if not target or not instance_id or redis_client is None:
        return None

    key = _EVENT_BLOCKS_HASH_FMT.format(instance_id=instance_id)
    try:
        mapping = await redis_client.hgetall(key)
    except Exception:
        logger.exception(
            "event_blocks resolver: hgetall failed instance=%s key=%s",
            instance_id, key,
        )
        return None

    if not mapping:
        await _request_scan_refresh(redis_client, instance_id)
        logger.info(
            "event_blocks resolver: hash empty instance=%s target=%s — refresh requested",
            instance_id, target,
        )
        return None

    for raw_idx, raw_val in mapping.items():
        idx = _decode(raw_idx)
        val = _decode(raw_val)
        if val == target and idx:
            return [f"main_city.event.block.{idx}"]

    # Target not currently in any block — either event isn't active, or our
    # map is stale (block got reassigned). Trigger a refresh and fail this
    # route; next attempt after scan will succeed or stay None correctly.
    await _request_scan_refresh(redis_client, instance_id)
    logger.info(
        "event_blocks resolver: target %s not in map instance=%s entries=%s",
        target, instance_id, {_decode(k): _decode(v) for k, v in mapping.items()},
    )
    return None


register_edge_resolver("event_blocks", resolve_event_block)
