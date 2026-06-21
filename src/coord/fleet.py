"""Fleet registry + heartbeat + ``fid → instance`` reverse index (async IO).

The fleet *view* is derived from the existing ``wos:instance:<id>:state`` hashes
(coord only adds a few fields), so there is no parallel source of truth. The
reverse index (``wos:coord:fid_active``) answers "which device is this account
ACTIVE on right now, and is it online?" — advisory and self-correcting: the
real source of truth is each instance's own ``active_player`` field, which only
that instance's worker writes.
"""
from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from . import keys
from .models import FleetView, InstanceSnapshot
from .redis_io import decode_hash, decode_str

if TYPE_CHECKING:
    from collections.abc import Iterable

    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Delete a fid's reverse-index entry only if it still points at *this* instance
# (by the "<iid>|<ts>" prefix) — used when a worker switches away from a fid, so
# it can't clobber a claim the fid legitimately re-acquired on another device.
_CAD_DELETE_OWN_LUA = """
local v = redis.call('HGET', KEYS[1], ARGV[1])
if v then
    local iid = string.match(v, '^(.-)|')
    if iid == ARGV[2] then
        return redis.call('HDEL', KEYS[1], ARGV[1])
    end
end
return 0
"""

# Delete a fid entry only if the stored value is byte-for-byte the one we read
# (used by reap_stale, so a fresh re-claim between read and delete survives).
_HDEL_IF_EQ_LUA = """
if redis.call('HGET', KEYS[1], ARGV[1]) == ARGV[2] then
    return redis.call('HDEL', KEYS[1], ARGV[1])
end
return 0
"""


class Fleet:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._cad_delete_own = redis.register_script(_CAD_DELETE_OWN_LUA)
        self._hdel_if_eq = redis.register_script(_HDEL_IF_EQ_LUA)

    # --- heartbeat / writers --------------------------------------------------
    async def publish_heartbeat(
        self,
        instance_id: str,
        *,
        active_player: str,
        now: float,
        prev_fid: str | None = None,
        alliance_tag: str | None = None,
        game: str | None = None,
        march_slots_total: int | None = None,
        march_slots_free: int | None = None,
    ) -> None:
        """Write coord fields onto the instance-state hash + refresh the reverse
        index for the active fid. ``prev_fid`` (the worker's last active player)
        is compare-and-deleted when the active player changed this tick."""
        mapping: dict[str, str] = {keys.FIELD_COORD_SEEN_AT: str(float(now))}
        if alliance_tag is not None:
            mapping[keys.FIELD_ALLIANCE_TAG] = alliance_tag
        if game is not None:
            mapping[keys.FIELD_GAME] = game
        if march_slots_total is not None:
            mapping[keys.FIELD_MARCH_SLOTS_TOTAL] = str(int(march_slots_total))
        if march_slots_free is not None:
            mapping[keys.FIELD_MARCH_SLOTS_FREE] = str(int(march_slots_free))
        await self._redis.hset(keys.instance_state_key(instance_id), mapping=mapping)

        fid = str(active_player or "")
        if prev_fid and prev_fid != fid:
            await self._cad_delete_own(keys=[keys.FID_ACTIVE_KEY], args=[prev_fid, instance_id])
        if fid:
            await self._redis.hset(
                keys.FID_ACTIVE_KEY, fid, f"{instance_id}|{float(now)!s}"
            )

    async def set_march_slots(self, instance_id: str, *, total: int, free: int) -> None:
        """Populate march-slot fields (the scheduler already computes these per
        player; keeps the worker hot loop free of resource-table loads)."""
        await self._redis.hset(
            keys.instance_state_key(instance_id),
            mapping={
                keys.FIELD_MARCH_SLOTS_TOTAL: str(int(total)),
                keys.FIELD_MARCH_SLOTS_FREE: str(int(free)),
            },
        )

    async def clear_fid(self, instance_id: str, fid: str) -> None:
        """Compare-and-delete this instance's own reverse-index claim on ``fid``."""
        if fid:
            await self._cad_delete_own(keys=[keys.FID_ACTIVE_KEY], args=[str(fid), instance_id])

    # --- readers --------------------------------------------------------------
    async def snapshot(self, instance_ids: Iterable[str], *, now: float) -> FleetView:
        """Build a :class:`FleetView` for the given instance ids (pipelined)."""
        ids = [str(i) for i in instance_ids if str(i)]
        if not ids:
            return FleetView(())
        pipe = self._redis.pipeline(transaction=False)
        for iid in ids:
            pipe.hgetall(keys.instance_state_key(iid))
        rows = await pipe.execute()
        snaps = [
            InstanceSnapshot.from_hash(iid, decode_hash(row), now=now)
            for iid, row in zip(ids, rows, strict=False)
        ]
        return FleetView(tuple(snaps))

    async def snapshot_all(self, *, now: float) -> FleetView:
        """Scan every ``wos:instance:*:state`` hash (for the API; the scheduler
        prefers :meth:`snapshot` with its known instance ids)."""
        ids: list[str] = []
        async for key in self._redis.scan_iter(match="wos:instance:*:state"):
            ks = decode_str(key)
            # wos:instance:<id>:state
            parts = ks.split(":")
            if len(parts) >= 4:
                ids.append(":".join(parts[2:-1]))
        return await self.snapshot(ids, now=now)

    async def resolve_fid(self, fid: str, *, now: float) -> tuple[str | None, bool]:
        """Return ``(instance_id_or_None, online)`` for a fid.

        ``instance_id`` is the device the reverse index claims hosts it (or None
        if unclaimed). ``online`` is True only if that instance is fresh AND still
        reports this fid as its ``active_player`` — i.e. the claim is valid.
        """
        fid = str(fid or "")
        if not fid:
            return (None, False)
        raw = await self._redis.hget(keys.FID_ACTIVE_KEY, fid)
        val = decode_str(raw)
        if not val:
            return (None, False)
        iid = val.split("|", 1)[0]
        if not iid:
            return (None, False)
        # Validate the claim against the instance's own (source-of-truth) fields.
        got = await self._redis.hmget(
            keys.instance_state_key(iid),
            [keys.FIELD_ACTIVE_PLAYER, keys.FIELD_COORD_SEEN_AT],
        )
        active = decode_str(got[0] if got else None)
        seen = decode_str(got[1] if got and len(got) > 1 else None)
        try:
            seen_f = float(seen) if seen else 0.0
        except (TypeError, ValueError):
            seen_f = 0.0
        fresh = seen_f > 0.0 and (now - seen_f) <= keys.FLEET_STALE_AFTER_S
        online = fresh and active == fid
        return (iid, online)

    async def reap_stale(self, *, now: float) -> int:
        """Prune reverse-index entries whose owning instance is stale or no longer
        reports the fid as active. Returns the number pruned. Runs from the
        scheduler tick (cross-instance housekeeping)."""
        raw = await self._redis.hgetall(keys.FID_ACTIVE_KEY)
        entries = decode_hash(raw)
        if not entries:
            return 0
        reaped = 0
        for fid, val in entries.items():
            iid = val.split("|", 1)[0]
            if not iid:
                continue
            got = await self._redis.hmget(
                keys.instance_state_key(iid),
                [keys.FIELD_ACTIVE_PLAYER, keys.FIELD_COORD_SEEN_AT],
            )
            active = decode_str(got[0] if got else None)
            seen = decode_str(got[1] if got and len(got) > 1 else None)
            try:
                seen_f = float(seen) if seen else 0.0
            except (TypeError, ValueError):
                seen_f = 0.0
            stale = not (seen_f > 0.0 and (now - seen_f) <= keys.FLEET_STALE_AFTER_S)
            if stale or active != fid:
                rv = await self._hdel_if_eq(keys=[keys.FID_ACTIVE_KEY], args=[fid, val])
                with contextlib.suppress(TypeError, ValueError):
                    reaped += int(rv)
        return reaped
