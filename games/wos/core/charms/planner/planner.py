"""Value-greedy Chief Charm planner: which charm slot to raise next.

Pure decision over the shared upgrade table (:mod:`model`), the per-slot levels the
player owns, and current material balances. Gated by the Furnace-25 unlock. Among
the 18 slots it ranks each next-level step by value (even-leveling × composition ×
role) and picks the highest-value affordable one. :func:`charm_roadmap` totals the
materials/power to bring every slot to a target level — the calculator's headline
"how much to develop my charms" answer. Live readers (per-slot levels) are deferred.

Resource keys: ``charm_guide`` / ``charm_design`` / ``charm_secrets`` (the shared
material pool, matching db/items/charm_*.yaml).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .model import load_charm_data
from .policy import charm_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import CharmData

# Plan reasons
SELECTED = "selected"
LOCKED = "locked"                       # Chief Charms not unlocked yet (Furnace < 25)
INSUFFICIENT_RESOURCES = "insufficient_resources"
NONE = "none"                           # every slot already at the target/max


@dataclass(frozen=True, slots=True)
class CharmCandidate:
    slot_id: str              # "infantry_1" … "marksman_6"
    troop_type: str           # infantry | lancer | marksman
    to_level: int
    value: float
    cost: Mapping[str, int]   # charm_guide / charm_design / charm_secrets
    power_gain: int           # power delta of this step (0 where the source lacks power)
    affordable: bool


@dataclass(frozen=True, slots=True)
class CharmPlan:
    step: CharmCandidate | None
    reason: str
    candidates: tuple[CharmCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CharmRoadmap:
    """Totals to bring every slot to a target level (the calculator's output)."""

    cost: Mapping[str, int]   # total materials
    power_gain: int           # total power added (where the source has power)
    steps: int                # number of level-ups


def _level(owned: Mapping[str, int], slot_id: str) -> int:
    try:
        return int(owned.get(slot_id, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _power_gain(data: CharmData, from_level: int, to_level: int) -> int:
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
    data: CharmData | None = None,
) -> CharmPlan:
    """Pick the next charm level-up by value, within the material budget.

    ``owned`` maps ``slot_id`` → current charm level (0 = level 0 / not started).
    ``furnace_level`` gates the feature (Chief Charms unlock at Furnace 25); pass
    ``None`` to skip the gate. ``max_level`` caps the target (defaults to the table's).
    """
    d = data if data is not None else load_charm_data()
    if furnace_level is not None and furnace_level < d.unlock_furnace_level:
        return CharmPlan(None, LOCKED)
    cap = min(int(max_level), d.max_level) if max_level is not None else d.max_level

    candidates: list[CharmCandidate] = []
    best: CharmCandidate | None = None
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
        value = charm_value(troop_type, nxt, max_level=cap, role=role, target=target)
        affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
        cand = CharmCandidate(
            slot_id=slot_id, troop_type=troop_type, to_level=nxt, value=value,
            cost=cost, power_gain=_power_gain(d, cur, nxt), affordable=affordable,
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
    return CharmPlan(step=best, reason=reason, candidates=tuple(candidates[:8]))


def charm_roadmap(
    owned: Mapping[str, int],
    target_level: int,
    *,
    data: CharmData | None = None,
) -> CharmRoadmap:
    """Total materials + power + step count to bring every slot up to ``target_level``."""
    d = data if data is not None else load_charm_data()
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
    return CharmRoadmap(cost=cost, power_gain=power, steps=steps)
