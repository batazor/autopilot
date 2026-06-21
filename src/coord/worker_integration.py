"""``CoordWorkerMixin`` — the worker side of the coordination layer.

Mirrors ``worker.instance_worker_ui.InstanceWorkerUiMixin``: a sibling drain
(``_drain_directives``) consumed at the SAME safe points as UI commands (between
tasks, never mid-task), plus a fleet heartbeat published next to the existing
``last_seen_at`` write. Engine-level, so it declares the host attributes it needs
rather than importing the worker (no coord → worker dependency).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from . import handlers, keys
from .bus import DirectiveBus
from .fleet import Fleet
from .models import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    DirectiveStatus,
)
from .redis_io import decode_str

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from .models import Directive

logger = logging.getLogger(__name__)


class CoordWorkerMixin:
    # Provided by InstanceWorker (declared, not imported, to avoid a back-edge).
    _cfg: Any
    _redis: aioredis.Redis | None
    _queue: Any

    # Coord state (lazily initialised once the Redis client exists).
    _coord_fleet: Fleet | None = None
    _coord_bus: DirectiveBus | None = None
    _coord_last_fid: str = ""

    def _coord_ready(self) -> bool:
        if self._redis is None:
            return False
        if self._coord_fleet is None or self._coord_bus is None:
            self._coord_fleet = Fleet(self._redis)
            self._coord_bus = DirectiveBus(self._redis)
        return True

    def _coord_inbox_key(self) -> str:
        """The per-instance directive inbox key (for the two-key BRPOP wake)."""
        return keys.directive_inbox_key(self._cfg.instance_id)

    async def _handle_directive_raw(self, raw: str | bytes) -> None:
        """Parse + dispatch a single directive payload (the BRPOP wake path)."""
        from .models import Directive

        try:
            directive = Directive.from_json(raw)
        except Exception:
            logger.warning("coord: dropping malformed directive on wake")
            return
        await self._handle_directive(directive)

    async def _coord_read_active(self) -> str:
        if self._redis is None:
            return ""
        try:
            raw = await self._redis.hget(
                keys.instance_state_key(self._cfg.instance_id), keys.FIELD_ACTIVE_PLAYER
            )
        except Exception:
            return ""
        return decode_str(raw)

    async def _publish_fleet_heartbeat(self, now: float) -> None:
        """Write the coord heartbeat + refresh the fid→instance reverse index.

        alliance_tag / march slots are populated by the scheduler tick (it
        already loads player state + builds the per-player world), so the worker
        hot loop stays free of resource-table loads."""
        if not self._coord_ready():
            return
        assert self._coord_fleet is not None
        active = await self._coord_read_active()
        try:
            await self._coord_fleet.publish_heartbeat(
                self._cfg.instance_id,
                active_player=active,
                now=now,
                prev_fid=self._coord_last_fid or None,
                game=getattr(self._cfg, "game", None) or None,
            )
        except Exception:
            logger.debug("coord heartbeat failed", exc_info=True)
            return
        self._coord_last_fid = active

    async def _drain_directives(self) -> None:
        if not self._coord_ready():
            return
        assert self._coord_bus is not None
        try:
            directives = await self._coord_bus.drain(self._cfg.instance_id)
        except Exception:
            logger.debug("coord directive drain failed", exc_info=True)
            return
        for directive in directives:
            await self._handle_directive(directive)

    async def _handle_directive(self, directive: Directive) -> None:
        if not self._coord_ready():
            return
        assert self._coord_bus is not None
        bus = self._coord_bus
        iid = self._cfg.instance_id
        did = directive.directive_id
        now = time.time()

        if not await bus.claim(iid, directive.dedup_key()):
            await bus.audit_append(kind="skipped_dup", now=now, directive_id=did, target_iid=iid)
            return

        handler = handlers.get(directive.kind)
        if handler is None:
            await bus.set_status(
                DirectiveStatus(did, iid, STATUS_FAILED, error="unknown_kind"), now=now
            )
            await bus.audit_append(kind="failed", now=now, directive_id=did, error="unknown_kind")
            logger.warning("coord: unknown directive kind %r", directive.kind)
            return

        await bus.set_status(
            DirectiveStatus(did, iid, STATUS_RUNNING, started_at=now), now=now
        )
        await bus.audit_append(
            kind="received", now=now, directive_id=did, type=directive.kind, target_iid=iid
        )
        ctx = handlers.HandlerContext(
            redis=self._redis,
            bus=bus,
            queue=self._queue,
            instance_id=iid,
            active_player=self._coord_last_fid,
        )
        try:
            result = await handler(ctx, directive)
        except Exception as exc:
            # A directive failure must never kill the worker loop.
            end = time.time()
            await bus.set_status(
                DirectiveStatus(did, iid, STATUS_FAILED, finished_at=end, error=str(exc)[:200]),
                now=end,
            )
            await bus.audit_append(
                kind="failed", now=end, directive_id=did, error=str(exc)[:200]
            )
            logger.warning("coord directive %r failed", directive.kind, exc_info=True)
            return

        end = time.time()
        await bus.set_status(
            DirectiveStatus(did, iid, STATUS_DONE, finished_at=end, result=result), now=end
        )
        await bus.audit_append(kind="done", now=end, directive_id=did, result=result)
