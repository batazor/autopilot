"""Value weighting + classification for the multi-track build planner.

The furnace-first chain ([:func:`planner.plan_next`]) is the *progression* track.
On top of it the planner runs economy/worker tracks so free construction queues
never idle: when the furnace pick is too expensive (or blocked), the queue builds
something useful instead — a resource producer, a Shelter (worker housing), or the
Storehouse (plunder-protection capacity).

Selection is value-greedy + role-biased (the user's choice): every candidate gets
a value, the top affordable+ready ones fill the free queues. Weights are
config-as-code; categories map to role categories so [[account-role-profiles]]
(farm → economy, fighter → battle) tilts the picks, while progression stays
universal. A role may also opt out of a building entirely via ``no_build`` — a
farm never upgrades the Storehouse so its resources stay raidable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from games.wos.core.roles import RoleProfile

# Track values (progression / bottleneck are role-independent — the spine).
PROGRESSION_WEIGHT = 100.0
BOTTLENECK_WEIGHT = 90.0          # a producer that unblocks the progression pick

# Economy / combat building base weights (before the role multiplier). The
# Storehouse (protection) sits lowest — a main upgrades it occasionally to raise
# the plunder-protected cap, never aggressively; a farm opts out entirely (see
# [[account-role-profiles]] ``no_build``) to stay raidable.
CATEGORY_WEIGHT = {"producer": 55.0, "shelter": 50.0, "camp": 60.0, "storehouse": 45.0}

# Which role category each building kind biases with.
KIND_ROLE_CATEGORY = {
    "producer": "economy", "shelter": "economy", "storehouse": "economy", "camp": "battle",
}

# Building classification (filtered against the graph at runtime, so listing an id
# the graph lacks is harmless).
PRODUCERS = ("sawmill", "hunters_hut", "coal_mine", "iron_mine")
CAMPS = ("infantry_camp", "lancer_camp", "marksman_camp")
PROTECTION = ("storehouse",)
SHELTER_ID = "shelter"

# Multi-instance buildings: one db spec, N independent plots in-game.
INSTANCES = {SHELTER_ID: 8}

# Bottleneck repair: which producer makes a given resource. Build costs are now
# decoded to canonical resource names (model.ITEM_RESOURCE), so a furnace pick that
# is short on, say, coal boosts the Coal Mine's value to unblock it. Meat and wood
# are cost-equal in the data, so a shortfall in one usually flags both → both
# producers get lifted, which is the right behaviour.
PRODUCER_BY_RESOURCE: dict[str, str] = {
    "meat": "hunters_hut",
    "wood": "sawmill",
    "coal": "coal_mine",
    "iron": "iron_mine",
}

# --- Event-points value bonus ------------------------------------------------
# While a construction-scoring window is live, tilt the queue toward higher-power
# upgrades (they net more event points). The uplift is normalised against the best
# candidate this pass (band-relative, so it works at both the 1-30 and Fire-Crystal
# power scales): the top-scoring upgrade gets the full EVENT_POINTS_WEIGHT, the rest
# scale linearly by their share. Bounded so it reorders within / just across a value
# tier without letting an economy build leap the furnace-first spine (weight 100).
EVENT_POINTS_WEIGHT = 25.0


def event_value_bonus(points: int, max_points: int) -> float:
    """Value uplift for an upgrade worth ``points``, relative to the pass best
    ``max_points`` (0 / no points → 0.0; the best candidate → full weight)."""
    if points <= 0 or max_points <= 0:
        return 0.0
    return EVENT_POINTS_WEIGHT * points / max_points


def building_value(kind: str, role: RoleProfile | None) -> float:
    """Base weight for an economy/combat building kind, biased by ``role``."""
    base = CATEGORY_WEIGHT.get(kind, 10.0)
    if role is not None:
        base *= role_multiplier(role, KIND_ROLE_CATEGORY.get(kind, "growth"))
    return base


def economy_kind(spec_id: str) -> str:
    """Classify an economy-track building id into its value ``kind``.

    Producers + the Storehouse share the ``economy`` role category but carry
    distinct base weights; the Shelter is worker housing.
    """
    if spec_id == SHELTER_ID:
        return "shelter"
    if spec_id in PROTECTION:
        return "storehouse"
    return "producer"


def instance_ids(spec_id: str) -> tuple[str, ...]:
    """Per-plot ids for a spec: ``shelter`` → ``shelter_1 … shelter_8``.

    Single-instance buildings return just their own id.
    """
    n = INSTANCES.get(spec_id, 1)
    if n <= 1:
        return (spec_id,)
    return tuple(f"{spec_id}_{i}" for i in range(1, n + 1))
