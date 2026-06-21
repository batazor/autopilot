"""Cross-instance directive bus (async IO).

* **Inbox** — a per-instance LIST (``LPUSH`` by poster, ``RPOP`` by the worker
  drain), mirroring the proven ``wos:ui:command:<id>`` channel. Single-consumer,
  no replay (a redelivered "switch account" is harmful).
* **Dedup** — a per-instance SADD-claim set makes redelivery idempotent.
* **Status** — a per-directive HASH the poster polls.
* **Audit** — an append-only STREAM (``XADD``) for durable, cursor-tailable
  observability of "who told whom what, and did it happen".
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from . import keys
from .models import Directive, DirectiveStatus
from .redis_io import decode_hash, decode_str
from .routing import resolve_targets

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from .models import FleetView

logger = logging.getLogger(__name__)


class DirectiveBus:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    # --- post / drain ---------------------------------------------------------
    async def post(
        self,
        directive: Directive,
        view: FleetView | None = None,
        *,
        now: float | None = None,
    ) -> list[str]:
        """Resolve the directive's target to instance ids, LPUSH each inbox, and
        audit. Returns the instance ids it was posted to (``[]`` if unroutable,
        e.g. a fid that's offline — the caller decides whether to switch/defer)."""
        from .models import FleetView as _FleetView

        ts = time.time() if now is None else now
        targets = resolve_targets(directive.target, view or _FleetView(()))
        payload = directive.to_json()
        for iid in targets:
            await self._redis.lpush(keys.directive_inbox_key(iid), payload)
            await self.audit_append(
                kind="posted",
                now=ts,
                directive_id=directive.directive_id,
                type=directive.kind,
                source=directive.source,
                target_iid=iid,
            )
        if not targets:
            await self.audit_append(
                kind="unrouted",
                now=ts,
                directive_id=directive.directive_id,
                type=directive.kind,
                source=directive.source,
                target=f"{directive.target.kind}:{directive.target.value}",
            )
        return targets

    async def drain(self, instance_id: str) -> list[Directive]:
        """RPOP every queued directive for an instance (FIFO vs LPUSH order)."""
        key = keys.directive_inbox_key(instance_id)
        out: list[Directive] = []
        while True:
            raw = await self._redis.rpop(key)
            if raw is None:
                break
            try:
                out.append(Directive.from_json(raw))
            except Exception:
                logger.warning("coord: dropping malformed directive on %s", instance_id)
        return out

    async def claim(self, instance_id: str, dedup_key: str) -> bool:
        """SADD-claim: True the first time a dedup key is seen on this instance,
        False on redelivery. Refreshes the set's TTL on each claim."""
        seen = keys.directive_seen_key(instance_id)
        added = await self._redis.sadd(seen, dedup_key)
        await self._redis.expire(seen, keys.DIRECTIVE_SEEN_TTL_S)
        try:
            return int(added) == 1
        except (TypeError, ValueError):
            return False

    # --- status ---------------------------------------------------------------
    async def set_status(self, status: DirectiveStatus, *, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        key = keys.directive_status_key(status.directive_id)
        mapping = {
            "instance_id": status.instance_id,
            "state": status.state,
            "started_at": str(status.started_at or ts),
            "finished_at": str(status.finished_at),
            "result": status.result,
            "error": status.error,
            "updated_at": str(ts),
        }
        await self._redis.hset(key, mapping=mapping)
        await self._redis.expire(key, keys.DIRECTIVE_STATUS_TTL_S)

    async def read_status(self, directive_id: str) -> DirectiveStatus | None:
        raw = decode_hash(await self._redis.hgetall(keys.directive_status_key(directive_id)))
        if not raw:
            return None
        return DirectiveStatus.from_hash(directive_id, raw)

    # --- audit ----------------------------------------------------------------
    async def audit_append(self, *, kind: str, now: float, **fields: Any) -> None:
        entry = {"ts": str(now), "kind": kind}
        for k, v in fields.items():
            if v is not None:
                entry[k] = str(v)
        try:
            await self._redis.xadd(
                keys.AUDIT_STREAM, entry, maxlen=keys.AUDIT_MAXLEN, approximate=True
            )
        except Exception:
            logger.debug("coord audit append failed (kind=%s)", kind, exc_info=True)

    async def read_audit(self, *, count: int = 100) -> list[dict[str, str]]:
        """Most-recent-first audit entries (XREVRANGE)."""
        try:
            rows = await self._redis.xrevrange(keys.AUDIT_STREAM, count=max(1, int(count)))
        except Exception:
            return []
        out: list[dict[str, str]] = []
        for entry_id, raw in rows:
            d = decode_hash(raw)
            d["id"] = decode_str(entry_id)
            out.append(d)
        return out
