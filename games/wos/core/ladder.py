"""Shared core for the "even-leveling ladder" investment planners.

The charm / chief-gear / hero-gear planners all make the same decision: every slot
climbs the *same* cost/power ladder, so the planner raises the most-lagging slot
first (cost rises faster than power), tilted by army composition + role. This module
holds that algorithm once — the value math, the greedy single-track ``plan_next``
loop, and the roadmap summation — so each domain is a thin wrapper that keeps its
branded dataclasses. (pets/troops are different algorithms and don't use this.)

A "ladder data" object here is any value exposing ``unlock_furnace_level``,
``max_level``, ``slots`` (``slot_id → troop_type``) and ``level(n)`` (→ an object
with ``cost`` and ``power``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from games.wos.core.roles import RoleProfile


def even_leveling_value(
    to_level: int,
    *,
    max_level: int,
    composition: float,
    base: float = 100.0,
    role: RoleProfile | None = None,
    role_category: str = "battle",
    normalize: bool = False,
) -> float:
    """Even-leveling value: lower target levels score higher, × composition × role.

    ``normalize=False`` → ``recency = max(1, max−to+1)`` (single-scale domains: charms,
    chief gear). ``normalize=True`` → ``(max−to+1)/max`` (hero gear, whose tracks live on
    different scales so recency must be comparable).
    """
    if normalize:
        recency = max(0.0, (max_level - int(to_level) + 1) / max(1, max_level))
    else:
        recency = max(1, max_level - int(to_level) + 1)
    value = base * composition * recency
    if role is not None:
        value *= role_multiplier(role, role_category)
    return value


def owned_level(owned: Mapping[str, int], slot_id: str) -> int:
    """Current level of a slot (0 / unparseable → 0)."""
    try:
        return int(owned.get(slot_id, 0) or 0)
    except (TypeError, ValueError):
        return 0


def power_gain(data: Any, from_level: int, to_level: int) -> int:
    """Power delta ``from_level`` → ``to_level`` (0 where either level lacks power)."""
    a = data.level(from_level)
    b = data.level(to_level)
    a_pow = a.power if a else 0
    if b is None or b.power is None or a_pow is None:
        return 0
    return max(0, b.power - a_pow)


def plan_ladder(
    owned: Mapping[str, int],
    resources: Mapping[str, int],
    *,
    data: Any,
    value_fn: Callable[..., float],
    make_candidate: Callable[..., Any],
    make_plan: Callable[[Any, str, tuple[Any, ...]], Any],
    reasons: tuple[str, str, str, str],
    furnace_level: int | None = None,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
    max_level: int | None = None,
) -> Any:
    """Greedy single-track ladder pick (the shared charm/chief-gear ``plan_next``).

    ``reasons`` = ``(SELECTED, LOCKED, INSUFFICIENT_RESOURCES, NONE)``. ``make_candidate``
    is ``(slot_id, troop_type, to_level, value, cost, power_gain, level, affordable) →
    Candidate``; ``make_plan`` is ``(step, reason, candidates) → Plan`` — both build the
    domain's branded types.
    """
    selected, locked, insufficient, none = reasons
    if furnace_level is not None and furnace_level < data.unlock_furnace_level:
        return make_plan(None, locked, ())
    cap = min(int(max_level), data.max_level) if max_level is not None else data.max_level

    candidates: list[Any] = []
    best: Any = None
    best_key: tuple[float, int] | None = None
    any_upgradable = False

    for slot_id, troop_type in data.slots.items():
        cur = owned_level(owned, slot_id)
        if cur >= cap:
            continue                                       # at the target/max
        any_upgradable = True
        nxt = cur + 1
        lvl = data.level(nxt)
        if lvl is None:
            continue
        cost = dict(lvl.cost)
        value = value_fn(troop_type, nxt, max_level=cap, role=role, target=target)
        affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
        cand = make_candidate(
            slot_id, troop_type, nxt, value, cost, power_gain(data, cur, nxt), lvl, affordable,
        )
        candidates.append(cand)
        if affordable:
            key = (value, -sum(cost.values()))             # value, then cheapest
            if best_key is None or key > best_key:
                best, best_key = cand, key

    candidates.sort(key=lambda c: (-c.value, c.slot_id))
    reason = selected if best is not None else (none if not any_upgradable else insufficient)
    return make_plan(best, reason, tuple(candidates[:8]))


def roadmap_ladder(
    owned: Mapping[str, int], target_level: int, *, data: Any
) -> tuple[dict[str, int], int, int]:
    """``(cost, power_gain, steps)`` to bring every slot up to ``target_level``."""
    cap = min(int(target_level), data.max_level)
    cost: dict[str, int] = {}
    power = 0
    steps = 0
    for slot_id in data.slots:
        cur = owned_level(owned, slot_id)
        for n in range(cur + 1, cap + 1):
            lvl = data.level(n)
            if lvl is None:
                continue
            for res, amt in lvl.cost.items():
                cost[res] = cost.get(res, 0) + amt
            power += power_gain(data, n - 1, n)
            steps += 1
    return cost, power, steps
