"""Farm-raid economics — is a raid worth it, and how big an army to send.

Pure. Reuses the troop-stats facts (``troops.yaml`` ``load``/``power`` per
``(type, tier, fc)``). The value model: a fighter loots a farm's *unprotected*
resources, capped by the carry capacity (``load``) of the troops it sends;
right-size the army to carry exactly the plunder while committing the least
power (keep the army home/safe). ``roi = value − cost`` feeds the matchmaker
(``matchmaking.plan_raids``) and the arbiter (``objective.campaign_priority``).

Current troop pool + farm resources + map distance are INPUTS (the troop reader,
resource read, and coord grid are deferred) — tests inject them; production wires
the readers later with no change here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from games.wos.core.resources.troop_stats import base_speed, load_troop_stats

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.resources.troop_stats import TroopStat

# A typed troop pool: (type, tier, fc) -> count.
TroopKey = tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class RaidValue:
    plunder: dict[str, int]            # resources actually carried (by type)
    carried_total: int                 # total carried
    value: float                       # weighted plunder worth
    cost: float                        # march + power-exposure opportunity cost
    roi: float                         # value − cost
    army: dict[TroopKey, int]          # right-sized troops to send
    army_load: int                     # total carry capacity sent
    army_power: int                    # power committed
    feasible: bool                     # carries something with a non-empty army


def total_load(army: Mapping[TroopKey, int], stats: Mapping[TroopKey, TroopStat]) -> int:
    return sum(cnt * stats[k].load for k, cnt in army.items() if k in stats)


def total_power(army: Mapping[TroopKey, int], stats: Mapping[TroopKey, TroopStat]) -> int:
    return sum(cnt * stats[k].power for k, cnt in army.items() if k in stats)


def lootable(
    resources: Mapping[str, int], protected_floor: Mapping[str, int] | int = 0
) -> dict[str, int]:
    """Resources above the protected floor (a farm keeps no storehouse, so most
    of its hoard is lootable). ``protected_floor`` may be per-resource or scalar."""
    out: dict[str, int] = {}
    for r, amt in resources.items():
        floor = (
            protected_floor.get(r, 0)
            if isinstance(protected_floor, dict)
            else int(protected_floor)
        )
        out[r] = max(0, int(amt) - int(floor))
    return out


def size_army_for_load(
    available: Mapping[TroopKey, int],
    needed_load: int,
    stats: Mapping[TroopKey, TroopStat],
) -> dict[TroopKey, int]:
    """Smallest committal of troops whose carry capacity covers ``needed_load``,
    preferring the most load-per-power units first (carry the plunder while
    keeping power — and risk — at home). Capped by what's available."""
    if needed_load <= 0:
        return {}
    order = sorted(
        (k for k in available if k in stats and available[k] > 0 and stats[k].load > 0),
        key=lambda k: (-(stats[k].load / max(1, stats[k].power)), -stats[k].load, k),
    )
    army: dict[TroopKey, int] = {}
    acc = 0
    for k in order:
        if acc >= needed_load:
            break
        unit_load = stats[k].load
        want = -(-(needed_load - acc) // unit_load)  # ceil division
        take = min(available[k], want)
        if take <= 0:
            continue
        army[k] = take
        acc += take * unit_load
    return army


def raid_value(
    farm_resources: Mapping[str, int],
    attacker_available: Mapping[TroopKey, int],
    *,
    stats: Mapping[TroopKey, TroopStat] | None = None,
    weights: Mapping[str, float] | None = None,
    protected_floor: Mapping[str, int] | int = 0,
    distance: float = 0.0,
    speed: int | None = None,
    march_cost_per_dist: float = 0.0,
    power_cost_factor: float = 0.0,
) -> RaidValue:
    """Expected plunder (carried, weighted) minus opportunity cost.

    ``value`` weights carried resources by ``weights`` (default 1.0 each — value
    == carried). ``cost`` = round-trip march + committed-power exposure (both
    factors default 0, so ``roi == value`` until tuned / coords land)."""
    stats = stats if stats is not None else load_troop_stats()
    speed = speed if speed is not None else base_speed()

    loot = lootable(farm_resources, protected_floor)
    loot_total = sum(loot.values())
    army = size_army_for_load(attacker_available, loot_total, stats)
    capacity = total_load(army, stats)
    carried_total = min(loot_total, capacity)

    plunder: dict[str, int] = {}
    if loot_total > 0 and carried_total > 0:
        for r, amt in loot.items():
            plunder[r] = amt * carried_total // loot_total

    w = weights or {}
    value = float(sum(cnt * float(w.get(r, 1.0)) for r, cnt in plunder.items()))
    army_power = total_power(army, stats)
    cost = 2.0 * float(distance) * float(march_cost_per_dist) + army_power * float(
        power_cost_factor
    )
    return RaidValue(
        plunder=plunder,
        carried_total=carried_total,
        value=value,
        cost=cost,
        roi=value - cost,
        army=army,
        army_load=capacity,
        army_power=army_power,
        feasible=carried_total > 0 and bool(army),
    )
