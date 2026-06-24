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

from games.wos.core.ladder import plan_ladder, roadmap_ladder

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


_REASONS = (SELECTED, LOCKED, INSUFFICIENT_RESOURCES, NONE)


def _make_candidate(
    slot_id: str, troop_type: str, to_level: int, value: float,
    cost: Mapping[str, int], power_gain: int, _lvl: object, affordable: bool,
) -> CharmCandidate:
    return CharmCandidate(
        slot_id=slot_id, troop_type=troop_type, to_level=to_level, value=value,
        cost=cost, power_gain=power_gain, affordable=affordable,
    )


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
    return plan_ladder(
        owned, resources, data=data if data is not None else load_charm_data(),
        value_fn=charm_value, make_candidate=_make_candidate,
        make_plan=lambda step, reason, cands: CharmPlan(step, reason, cands),
        reasons=_REASONS, furnace_level=furnace_level, role=role, target=target,
        max_level=max_level,
    )


def charm_roadmap(
    owned: Mapping[str, int],
    target_level: int,
    *,
    data: CharmData | None = None,
) -> CharmRoadmap:
    """Total materials + power + step count to bring every slot up to ``target_level``."""
    cost, power, steps = roadmap_ladder(
        owned, target_level, data=data if data is not None else load_charm_data(),
    )
    return CharmRoadmap(cost=cost, power_gain=power, steps=steps)
