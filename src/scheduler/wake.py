"""Wake-up channel for the scheduler's event-driven loop.

The scheduler blocks on this list (BLPOP) so producers — UI commands,
worker task completion — can pull the next optimization forward instead
of waiting for the 30s heartbeat tick.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis as _redis_sync
    import redis.asyncio as _redis_async

WAKE_CHANNEL = "wos:ui:command:scheduler"


async def wake_scheduler_async(
    client: _redis_async.Redis, cmd: dict[str, object] | None = None
) -> None:
    payload = json.dumps(cmd or {"cmd": "wake"})
    await client.lpush(WAKE_CHANNEL, payload)


def wake_scheduler(client: _redis_sync.Redis, cmd: dict[str, object] | None = None) -> None:
    payload = json.dumps(cmd or {"cmd": "wake"})
    client.lpush(WAKE_CHANNEL, payload)
