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

from games.wos.core.ladder import plan_ladder, roadmap_ladder

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


_REASONS = (SELECTED, LOCKED, INSUFFICIENT_RESOURCES, NONE)


def _make_candidate(
    slot_id: str, troop_type: str, to_level: int, value: float,
    cost: Mapping[str, int], power_gain: int, lvl: object, affordable: bool,
) -> GearCandidate:
    return GearCandidate(
        slot_id=slot_id, troop_type=troop_type, to_level=to_level, label=lvl.label,
        value=value, cost=cost, power_gain=power_gain, affordable=affordable,
    )


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
    return plan_ladder(
        owned, resources, data=data if data is not None else load_gear_data(),
        value_fn=gear_value, make_candidate=_make_candidate,
        make_plan=lambda step, reason, cands: GearPlan(step, reason, cands),
        reasons=_REASONS, furnace_level=furnace_level, role=role, target=target,
        max_level=max_level,
    )


def gear_roadmap(
    owned: Mapping[str, int],
    target_level: int,
    *,
    data: GearData | None = None,
) -> GearRoadmap:
    """Total materials + power + step count to bring every piece up to ``target_level``."""
    cost, power, steps = roadmap_ladder(
        owned, target_level, data=data if data is not None else load_gear_data(),
    )
    return GearRoadmap(cost=cost, power_gain=power, steps=steps)
