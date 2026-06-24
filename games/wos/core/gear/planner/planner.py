"""Value-greedy Chief Gear planner: which gear piece to upgrade next.

Pure decision over the shared upgrade ladder (:mod:`model`), the per-piece levels
the player owns, and current material balances. Gated by the Furnace-22 unlock.
Among the 6 pieces it ranks each next step by value (even-leveling × composition ×
role) and picks the highest-value affordable one. :func:`gear_roadmap` totals the
materials/power to bring every piece to a target step — the calculator's headline
"how much to develop my gear" answer. Live readers (per-piece level) are deferred.

Resource keys: ``hardened_alloy`` / ``polishing_solution`` / ``design_plans`` /
``lunar_amber`` (the shared material pool, matching db/items/*.yaml).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .model import load_gear_data
from .policy import gear_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import GearData

# Plan reasons
SELECTED = "selected"
LOCKED = "locked"                       # Chief Gear not unlocked yet (Furnace < 22)
INSUFFICIENT_RESOURCES = "insufficient_resources"
NONE = "none"                           # every piece already at the target/max


@dataclass(frozen=True, slots=True)
class GearCandidate:
    slot_id: str              # "gloves_belt_infantry" … "goggles_boots_marksman"
    troop_type: str           # infantry | lancer | marksman
    to_level: int             # ordinal step
    label: str                # in-game tier+star ("blue_2", "pink_t3_4")
    value: float
    cost: Mapping[str, int]   # hardened_alloy / polishing_solution / design_plans / lunar_amber
    power_gain: int           # power delta of this step
    affordable: bool


@dataclass(frozen=True, slots=True)
class GearPlan:
    step: GearCandidate | None
    reason: str
    candidates: tuple[GearCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class GearRoadmap:
    """Totals to bring every piece to a target step (the calculator's output)."""

    cost: Mapping[str, int]   # total materials
    power_gain: int           # total power added
    steps: int                # number of upgrade steps


def _level(owned: Mapping[str, int], slot_id: str) -> int:
    try:
        return int(owned.get(slot_id, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _power_gain(data: GearData, from_level: int, to_level: int) -> int:
    """Power delta from ``from_level`` → ``to_level`` (0 where either lacks power)."""
    a = data.level(from_level)
    b = data.level(to_level)
    a_pow = a.power if a else 0
    if b is None or b.power is None or a_pow is None:
        return 0
    return max(0, b.power - a_pow)


def plan_next(
    owned: Mapping[str, int],
    resources: Mapping[str, int],
    *,
    furnace_level: int | None = None,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
    max_level: int | None = None,
    data: GearData | None = None,
) -> GearPlan:
    """Pick the next gear upgrade by value, within the material budget.

    ``owned`` maps ``slot_id`` → current ordinal step (0 = not started). ``furnace_level``
    gates the feature (Chief Gear unlocks at Furnace 22); pass ``None`` to skip the gate.
    ``max_level`` caps the target (defaults to the ladder's).
    """
    d = data if data is not None else load_gear_data()
    if furnace_level is not None and furnace_level < d.unlock_furnace_level:
        return GearPlan(None, LOCKED)
    cap = min(int(max_level), d.max_level) if max_level is not None else d.max_level

    candidates: list[GearCandidate] = []
    best: GearCandidate | None = None
    best_key: tuple[float, int] | None = None
    any_upgradable = False

    for slot_id, troop_type in d.slots.items():
        cur = _level(owned, slot_id)
        if cur >= cap:
            continue                                       # at the target/max
        any_upgradable = True
        nxt = cur + 1
        lvl = d.level(nxt)
        if lvl is None:
            continue
        cost = dict(lvl.cost)
        value = gear_value(troop_type, nxt, max_level=cap, role=role, target=target)
        affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
        cand = GearCandidate(
            slot_id=slot_id, troop_type=troop_type, to_level=nxt, label=lvl.label,
            value=value, cost=cost, power_gain=_power_gain(d, cur, nxt), affordable=affordable,
        )
        candidates.append(cand)
        if affordable:
            key = (value, -sum(cost.values()))             # value, then cheapest
            if best_key is None or key > best_key:
                best, best_key = cand, key

    candidates.sort(key=lambda c: (-c.value, c.slot_id))
    if best is not None:
        reason = SELECTED
    elif not any_upgradable:
        reason = NONE
    else:
        reason = INSUFFICIENT_RESOURCES
    return GearPlan(step=best, reason=reason, candidates=tuple(candidates[:8]))


def gear_roadmap(
    owned: Mapping[str, int],
    target_level: int,
    *,
    data: GearData | None = None,
) -> GearRoadmap:
    """Total materials + power + step count to bring every piece up to ``target_level``."""
    d = data if data is not None else load_gear_data()
    cap = min(int(target_level), d.max_level)
    cost: dict[str, int] = {}
    power = 0
    steps = 0
    for slot_id in d.slots:
        cur = _level(owned, slot_id)
        for n in range(cur + 1, cap + 1):
            lvl = d.level(n)
            if lvl is None:
                continue
            for res, amt in lvl.cost.items():
                cost[res] = cost.get(res, 0) + amt
            power += _power_gain(d, n - 1, n)
            steps += 1
    return GearRoadmap(cost=cost, power_gain=power, steps=steps)
