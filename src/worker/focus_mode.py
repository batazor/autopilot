"""Focus mode — run **only** one scenario on an instance, nothing autonomous.

Focus mode is the base primitive behind "point-wise" scenario launch (the
fish-detect Play button, ``botctl run --focus``, the generic /run launcher). It
is stored as three fields on the per-instance state hash
``wos:instance:<id>:state`` so that **any** worker — isolated (``instance_runner``)
or supervisor-spawned — honours it by reading Redis each tick, with no second
worker and no per-process config:

* ``focus_scenario`` — the scenario key to run exclusively (empty ⇒ normal
  autopilot).
* ``focus_player`` — optional player id for account-level focus scenarios.
* ``focus_at`` — unix timestamp the focus was set (observability / UI).

When ``focus_scenario`` is non-empty a worker:

* suppresses every autonomous enqueue (startup seed, ``who_i_am`` identity
  probe, overlay ``pushScenario`` and inline taps);
* pops **only** queue items whose scenario matches ``focus_scenario`` — leftover
  cron work is ignored, never executed.

This module is the single source of truth for reading/writing those fields.
Sync helpers serve the API / scheduler / CLI; async helpers serve the worker
(``redis.asyncio``).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis
    import redis.asyncio as aioredis

FOCUS_SCENARIO_FIELD = "focus_scenario"
FOCUS_PLAYER_FIELD = "focus_player"
FOCUS_AT_FIELD = "focus_at"

_FOCUS_FIELDS = (FOCUS_SCENARIO_FIELD, FOCUS_PLAYER_FIELD, FOCUS_AT_FIELD)


def _state_key(instance_id: str) -> str:
    return f"wos:instance:{instance_id}:state"


def _decode(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return str(value).strip()


# --------------------------------------------------------------------------- #
# Sync (API / scheduler / CLI)
# --------------------------------------------------------------------------- #
def read_focus(client: redis.Redis, instance_id: str) -> tuple[str, str]:
    """Return ``(focus_scenario, focus_player)`` for an instance (sync)."""
    scenario, player = client.hmget(
        _state_key(instance_id), FOCUS_SCENARIO_FIELD, FOCUS_PLAYER_FIELD
    )
    return _decode(scenario), _decode(player)


def set_focus(
    client: redis.Redis,
    instance_id: str,
    *,
    scenario: str,
    player: str = "",
) -> None:
    """Pin an instance to run only ``scenario`` (sync)."""
    client.hset(
        _state_key(instance_id),
        mapping={
            FOCUS_SCENARIO_FIELD: str(scenario or "").strip(),
            FOCUS_PLAYER_FIELD: str(player or "").strip(),
            FOCUS_AT_FIELD: str(time.time()),
        },
    )


def clear_focus(client: redis.Redis, instance_id: str) -> None:
    """Clear focus mode, returning the instance to normal autopilot (sync)."""
    client.hdel(_state_key(instance_id), *_FOCUS_FIELDS)


# --------------------------------------------------------------------------- #
# Async (worker)
# --------------------------------------------------------------------------- #
async def read_focus_async(
    client: aioredis.Redis, instance_id: str
) -> tuple[str, str]:
    """Return ``(focus_scenario, focus_player)`` for an instance (async)."""
    scenario, player = await client.hmget(
        _state_key(instance_id), FOCUS_SCENARIO_FIELD, FOCUS_PLAYER_FIELD
    )
    return _decode(scenario), _decode(player)
