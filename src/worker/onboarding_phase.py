"""Shared onboarding-phase signal for the worker.

The first-run tutorial needs the popup dismissers and the identity probe to
stand down. Onboarding is over once *either* exit signal fires:

* **A resolved player** (``active_player`` set) — ``who_i_am`` only resolves a
  real gamer id for an established account; a fresh tutorial never has one.
* **The Sawmill build** (``buildings.levels.sawmill`` >= 1) — the primary exit
  for a fresh account the bot drives through the tutorial itself; the onboarding
  build-recorder writes it the moment the Sawmill goes up.

The Sawmill signal alone (the original design, replacing an even older
``furnace < 5`` proxy) wedged *developed accounts the bot never personally
onboarded* in onboarding forever: they come up with a resolved player but no
bot-recorded Sawmill, so the popup dismissers stayed gated off and login
purchase modals stacked up and blocked the worker. The ``active_player`` signal
closes that gap. The signals live in the Redis instance-state hash so the check
stays cheap on the rolling hot path.
"""
from __future__ import annotations

from typing import Any

# Canonical building id whose presence marks the end of onboarding, and the
# instance-state hash field the recorder mirrors it to.
ONBOARDING_EXIT_BUILDING = "sawmill"
ONBOARDING_EXIT_FIELD = f"buildings.levels.{ONBOARDING_EXIT_BUILDING}"
# A resolved real player is the other, equivalent onboarding-exit signal.
ACTIVE_PLAYER_FIELD = "active_player"


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    return (raw.decode() if isinstance(raw, bytes) else str(raw)).strip()


async def onboarding_active(redis: Any, instance_id: str) -> bool:
    """True while the first-run tutorial is still running for ``instance_id``.

    Onboarding is over once a real player is resolved (``active_player`` set) OR
    the Sawmill is recorded (``buildings.levels.sawmill`` >= 1). Returns
    ``False`` when there is no Redis (degraded / unit-test mode) so the
    dismissers keep their pre-existing behaviour.
    """
    if redis is None:
        return False
    try:
        active_player_raw, sawmill_raw = await redis.hmget(
            f"wos:instance:{instance_id}:state",
            [ACTIVE_PLAYER_FIELD, ONBOARDING_EXIT_FIELD],
        )
    except Exception:
        return False
    # A resolved real player means the account is established — not onboarding.
    if _decode(active_player_raw):
        return False
    val = _decode(sawmill_raw)
    try:
        return (int(val) if val else 0) < 1
    except ValueError:
        # Non-numeric junk in the field — treat as "not yet built" (onboarding).
        return True
