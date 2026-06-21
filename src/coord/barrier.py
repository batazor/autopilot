"""Redis-backed barrier / rendezvous (async IO) over the pure
:func:`coord.barrier_logic.evaluate` state machine.

The crux primitive for cross-account safety: a farm ``arrive``s "city_empty" and
the fighter ``wait``s for quorum before attacking; an event campaign holds all
accounts until everyone hits a phase checkpoint. Keys carry an ``EXPIRE`` tied to
the deadline, so an abandoned barrier self-heals (the next ``poll`` returns
TIMED_OUT and the keys expire on their own — no manual GC).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from . import barrier_logic, keys
from .models import (
    BARRIER_ABORTED,
    BARRIER_READY,
    BARRIER_TIMED_OUT,
    BARRIER_WAITING,
    BarrierSpec,
    BarrierState,
)
from .redis_io import decode_hash, decode_str

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Atomic arrive: record the party, then (re)compute readiness against the spec.
# Persists 'ready'/'aborted' (sticky); 'timed_out' is left to poll so the
# deadline stays the single source of truth.
_ARRIVE_LUA = """
redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
local spec = redis.call('HGET', KEYS[1], 'spec')
if not spec then return 'unknown' end
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'aborted' then return 'aborted' end
if status == 'ready' then return 'ready' end
local cfg = cjson.decode(spec)
local cnt = redis.call('HLEN', KEYS[2])
if cnt >= tonumber(cfg['required_n']) then
    redis.call('HSET', KEYS[1], 'status', 'ready')
    return 'ready'
end
if tonumber(ARGV[3]) >= tonumber(cfg['deadline_ts']) then
    return 'timed_out'
end
return 'waiting'
"""


class Barrier:
    def __init__(self, barrier_id: str, *, redis: aioredis.Redis) -> None:
        self._id = str(barrier_id)
        self._redis = redis
        self._key = keys.barrier_key(barrier_id)
        self._arrived_key = keys.barrier_arrived_key(barrier_id)
        self._channel = keys.barrier_events_channel(barrier_id)
        self._arrive_script = redis.register_script(_ARRIVE_LUA)

    async def create(self, spec: BarrierSpec, *, now: float) -> None:
        await self._redis.hset(
            self._key,
            mapping={"spec": spec.to_json(), "created_at": str(now), "status": BARRIER_WAITING},
        )
        ttl = max(1, int(spec.deadline_ts - now + keys.BARRIER_GRACE_S))
        await self._redis.expire(self._key, ttl)
        await self._redis.expire(self._arrived_key, ttl)

    async def arrive(self, party: str, *, now: float, note: str = "") -> str:
        value = f"{now}|{note}" if note else str(now)
        rv = await self._arrive_script(
            keys=[self._key, self._arrived_key], args=[str(party), value, now]
        )
        status = decode_str(rv)
        with contextlib.suppress(Exception):
            await self._redis.publish(self._channel, status)
        return status

    async def _spec_and_arrived(self) -> tuple[BarrierSpec | None, set[str], str]:
        raw = decode_hash(await self._redis.hgetall(self._key))
        if not raw or "spec" not in raw:
            return (None, set(), BARRIER_WAITING)
        spec = BarrierSpec.from_json(raw["spec"])
        arrived = decode_hash(await self._redis.hgetall(self._arrived_key))
        return (spec, set(arrived.keys()), raw.get("status", BARRIER_WAITING))

    async def poll(self, *, now: float) -> str:
        spec, arrived, status = await self._spec_and_arrived()
        if spec is None:
            return BARRIER_WAITING
        if status == BARRIER_ABORTED:
            return BARRIER_ABORTED
        if status == BARRIER_READY:
            return BARRIER_READY
        return barrier_logic.evaluate(spec, arrived, now)

    async def state(self, *, now: float) -> BarrierState | None:
        spec, arrived, status = await self._spec_and_arrived()
        if spec is None:
            return None
        if status not in (BARRIER_READY, BARRIER_ABORTED):
            status = barrier_logic.evaluate(spec, arrived, now)
        return BarrierState(
            spec=spec, arrived=tuple(sorted(arrived)), status=status
        )

    async def abort(self, *, reason: str = "") -> None:
        await self._redis.hset(self._key, "status", BARRIER_ABORTED)
        with contextlib.suppress(Exception):
            await self._redis.publish(self._channel, f"{BARRIER_ABORTED}:{reason}")

    async def wait(self, *, timeout_s: float, poll_interval_s: float = 0.25) -> str:
        """Poll until READY / ABORTED, or the barrier deadline / ``timeout_s``
        passes (whichever first → TIMED_OUT). Terminal states return immediately."""
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            outcome = await self.poll(now=time.time())
            if outcome in (BARRIER_READY, BARRIER_ABORTED, BARRIER_TIMED_OUT):
                return outcome
            if time.time() >= deadline:
                return BARRIER_TIMED_OUT
            await asyncio.sleep(poll_interval_s)
