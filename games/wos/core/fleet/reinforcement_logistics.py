"""Reinforcement logistics — can troops arrive in time, and how many to send.

Pure. Two questions when an ally is under attack:
1. **Arrive in time?** march time (distance / speed) vs the attack ETA.
2. **Flip the defense?** how much troop power closes the gap to
   ``attacker_power × margin`` given the current defender power.

Reuses troop-stats power. Distance / attack-ETA / current troops are INPUTS
(coord grid + rally-timer + troop readers deferred) — tests inject them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from games.wos.core.resources.troop_stats import base_speed, load_troop_stats

from .raid_economics import TroopKey, total_power

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.resources.troop_stats import TroopStat


def march_time_s(distance: float, speed: int) -> float:
    return float(distance) / max(1, int(speed))


def can_arrive(distance: float, speed: int, attack_eta_s: float) -> bool:
    """True if a march covering ``distance`` lands before the attack hits."""
    return march_time_s(distance, speed) <= float(attack_eta_s)


def size_reinforcement(
    needed_power: int,
    available: Mapping[TroopKey, int],
    stats: Mapping[TroopKey, TroopStat],
) -> dict[TroopKey, int]:
    """Fewest-unit committal reaching ``needed_power`` (strongest troops first),
    capped by what's available."""
    if needed_power <= 0:
        return {}
    order = sorted(
        (k for k in available if k in stats and available[k] > 0 and stats[k].power > 0),
        key=lambda k: (-stats[k].power, k),
    )
    army: dict[TroopKey, int] = {}
    acc = 0
    for k in order:
        if acc >= needed_power:
            break
        up = stats[k].power
        want = -(-(needed_power - acc) // up)  # ceil division
        take = min(available[k], want)
        if take <= 0:
            continue
        army[k] = take
        acc += take * up
    return army


@dataclass(frozen=True, slots=True)
class ReinforcePlan:
    army: dict[TroopKey, int] = field(default_factory=dict)
    power: int = 0
    needed_power: int = 0
    arrives_in_time: bool = False
    sufficient: bool = False      # closes the defense gap
    worth_sending: bool = False   # arrives AND helps


def plan_reinforcement(
    attacker_power: int,
    defender_power: int,
    available: Mapping[TroopKey, int],
    *,
    stats: Mapping[TroopKey, TroopStat] | None = None,
    speed: int | None = None,
    distance: float = 0.0,
    attack_eta_s: float = float("inf"),
    margin: float = 1.1,
) -> ReinforcePlan:
    """Plan a reinforcement: size the army to flip the defense and check it can
    arrive before the attack lands."""
    stats = stats if stats is not None else load_troop_stats()
    speed = speed if speed is not None else base_speed()

    target = math.ceil(attacker_power * margin)  # power we must reach to win
    needed = max(0, target - int(defender_power))
    army = size_reinforcement(needed, available, stats)
    power = total_power(army, stats)
    arrives = can_arrive(distance, speed, attack_eta_s)
    sufficient = (int(defender_power) + power) >= target
    return ReinforcePlan(
        army=army,
        power=power,
        needed_power=needed,
        arrives_in_time=arrives,
        sufficient=sufficient,
        worth_sending=arrives and power > 0 and sufficient,
    )
