"""Shared onboarding-phase signal for the worker.

The first-run tutorial needs the popup dismissers and the identity probe to
stand down. Onboarding is over once *any* exit signal fires:

* **A resolved player** (``active_player`` set) — ``who_i_am`` only resolves a
  real gamer id for an established account; a fresh tutorial never has one.
* **The Sawmill build** (``buildings.levels.sawmill`` >= 1) — the primary exit
  for a fresh account the bot drives through the tutorial itself; the onboarding
  build-recorder writes it the moment the Sawmill goes up.
* **A built-up Furnace** (``furnace`` >= ``FURNACE_ONBOARDING_EXIT_LEVEL``) —
  read durably for every established account, so it holds even when the bot
  never personally onboarded the account.

The Sawmill signal alone (the original design, replacing an even older
``furnace < 5`` proxy) wedged *developed accounts the bot never personally
onboarded* in onboarding forever: they come up with a resolved player but no
bot-recorded Sawmill, so the popup dismissers stayed gated off and login
purchase modals stacked up and blocked the worker. The ``active_player`` signal
was added to close that gap — but it only fires *after* ``who_i_am`` resolves,
which leaves a bootstrap hole: right after a worker restart a developed account
has neither a recorded Sawmill nor a resolved ``active_player`` yet, and if a
login purchase modal is up it keeps the screen UNKNOWN. The node-gated
``who_i_am`` cannot run from an unknown screen, so ``active_player`` never
resolves, and with the dismissers gated off the modal is never cleared — a
closed deadlock loop that parks the instance at idle. The furnace level closes
that hole: a real first-run tutorial sits below the original ``furnace < 5``
threshold. All three signals live in the Redis instance-state hash so the check
stays cheap on the rolling hot path.
"""
from __future__ import annotations

from typing import Any

# Canonical building id whose presence marks the end of onboarding, and the
# instance-state hash field the recorder mirrors it to.
ONBOARDING_EXIT_BUILDING = "sawmill"
ONBOARDING_EXIT_FIELD = f"buildings.levels.{ONBOARDING_EXIT_BUILDING}"
# A resolved real player is another equivalent onboarding-exit signal.
ACTIVE_PLAYER_FIELD = "active_player"
# A furnace past the early-tutorial threshold is a third onboarding-exit signal,
# read durably for every established account. ``buildings.levels.furnace`` is the
# durable mirror; ``buildings.furnace.level`` is the onboarding furnace reader's
# field — accept either. ``5`` matches the original ``furnace < 5`` onboarding
# proxy a real first-run tutorial never clears.
FURNACE_LEVEL_FIELDS = ("buildings.levels.furnace", "buildings.furnace.level")
FURNACE_ONBOARDING_EXIT_LEVEL = 5
# Field order requested from the state hash — exported so callers/tests stay in
# lockstep with what ``onboarding_active`` reads.
ONBOARDING_STATE_FIELDS = (
    ACTIVE_PLAYER_FIELD,
    ONBOARDING_EXIT_FIELD,
    *FURNACE_LEVEL_FIELDS,
)


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    return (raw.decode() if isinstance(raw, bytes) else str(raw)).strip()


def _max_level(raws: list[Any]) -> int:
    """Largest non-negative integer level among ``raws`` (junk/blank ignored)."""
    best = 0
    for raw in raws:
        val = _decode(raw)
        if not val:
            continue
        try:
            best = max(best, int(val))
        except ValueError:
            continue
    return best


async def onboarding_active(redis: Any, instance_id: str) -> bool:
    """True while the first-run tutorial is still running for ``instance_id``.

    Onboarding is over once *any* exit signal fires: a real player is resolved
    (``active_player`` set), the Sawmill is recorded
    (``buildings.levels.sawmill`` >= 1), or the Furnace has passed the
    early-tutorial threshold (``furnace`` >= ``FURNACE_ONBOARDING_EXIT_LEVEL``).
    Returns ``False`` when there is no Redis (degraded / unit-test mode) so the
    dismissers keep their pre-existing behaviour.
    """
    if redis is None:
        return False
    try:
        raws = await redis.hmget(
            f"wos:instance:{instance_id}:state",
            list(ONBOARDING_STATE_FIELDS),
        )
    except Exception:
        return False
    active_player_raw = raws[0] if len(raws) > 0 else None
    sawmill_raw = raws[1] if len(raws) > 1 else None
    furnace_raws = list(raws[2:])
    # A resolved real player means the account is established — not onboarding.
    if _decode(active_player_raw):
        return False
    # A built-up furnace means a developed account — not onboarding. Closes the
    # post-restart bootstrap hole (no player resolved yet, no bot-recorded
    # Sawmill) that otherwise deadlocks the instance behind a login modal.
    if _max_level(furnace_raws) >= FURNACE_ONBOARDING_EXIT_LEVEL:
        return False
    val = _decode(sawmill_raw)
    try:
        return (int(val) if val else 0) < 1
    except ValueError:
        # Non-numeric junk in the field — treat as "not yet built" (onboarding).
        return True
