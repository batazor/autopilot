"""Dynamic edge resolver: ``heroes → page.heroes.<id>`` via Redis hero positions.

Reads ``wos:instance:<id>:hero_grid_positions`` (populated by the
``scan_heroes_grid`` DSL exec handler) and returns the tap region for the
grid cell currently hosting the requested hero. Registers itself on
import — same wiring as :mod:`navigation.template_icon_resolver`.

Minimal C semantics:
* hash empty / target absent → enqueues a fresh ``scan_heroes_grid`` for
  the next overlay tick (best-effort, fire-and-forget) and returns ``None``
  so the caller can fail this attempt and retry later;
* hash present and target found → returns ``[heroes.grid.r{ri}c{ci}]``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from navigation.screen_graph import (
    DynamicEdgeSpec,
    Tap,
    register_edge_resolver,
)

logger = logging.getLogger(__name__)

_HERO_GRID_POSITIONS_HASH_FMT = "wos:instance:{instance_id}:hero_grid_positions"
_SCAN_RETRIGGER_KEY_FMT = "wos:instance:{instance_id}:hero_grid_scan_pending"
_SCAN_RETRIGGER_TTL_SECONDS = 60
"""Soft mutex so a missed-target route doesn't spam scan re-triggers."""

# Cell ids are stored as the compact ``r{ri}c{ci}`` string the scan
# handler emits; the resolver rejects anything that doesn't fit this
# shape so a garbled Redis value can't produce an invented region name.
_CELL_RE = re.compile(r"^r(\d+)c(\d+)$")


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw or "").strip()


async def _request_scan_refresh(redis_client: Any, instance_id: str) -> None:
    """Best-effort hint that the position hash is stale. Idempotent via a
    60s mutex so a tight retry loop doesn't enqueue dozens of scans."""
    if redis_client is None or not instance_id:
        return
    mutex = _SCAN_RETRIGGER_KEY_FMT.format(instance_id=instance_id)
    try:
        acquired = await redis_client.set(
            mutex, "1", ex=_SCAN_RETRIGGER_TTL_SECONDS, nx=True
        )
    except Exception:
        logger.debug(
            "hero_grid resolver: scan-mutex set failed instance=%s",
            instance_id,
            exc_info=True,
        )
        return
    if not acquired:
        return
    logger.info(
        "hero_grid resolver: requesting scan refresh instance=%s", instance_id
    )
    # The actual scan is pushed by an overlay rule on the ``heroes`` screen;
    # the mutex above just throttles. No direct enqueue here so we stay
    # decoupled from the scenario layer.


async def resolve_hero_grid(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    target = str(spec.get("target") or "").strip()
    if not target or not instance_id or redis_client is None:
        return None

    key = _HERO_GRID_POSITIONS_HASH_FMT.format(instance_id=instance_id)
    try:
        raw = await redis_client.hget(key, target)
    except Exception:
        logger.exception(
            "hero_grid resolver: hget failed instance=%s key=%s target=%s",
            instance_id, key, target,
        )
        return None

    cell = _decode(raw)
    if not cell:
        await _request_scan_refresh(redis_client, instance_id)
        logger.info(
            "hero_grid resolver: hero=%s not in hash instance=%s — refresh requested",
            target, instance_id,
        )
        return None

    if not _CELL_RE.match(cell):
        # Garbage in the hash — drop and force a refresh next attempt.
        await _request_scan_refresh(redis_client, instance_id)
        logger.warning(
            "hero_grid resolver: hero=%s malformed cell=%r instance=%s",
            target, cell, instance_id,
        )
        return None

    return [f"heroes.grid.{cell}"]


register_edge_resolver("hero_grid", resolve_hero_grid)
