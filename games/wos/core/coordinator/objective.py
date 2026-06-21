"""Cross-domain priority bands — how the coordinator compares apples to oranges.

Each domain's actions live in a base priority band on one common scale; the
account role tilts them (a farm lifts economy/gather, a fighter lifts raids/troops),
while progression + research stay universal (Growth). Adapters call
:func:`domain_priority` so a building upgrade, a research project and a beast raid
become directly comparable for :func:`coordinator.coordinate`.

Config-as-code; tune here. Research sits highest because its speed bonus compounds
across every other domain (the meta), then furnace progression, then combat /
economy by role.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from games.wos.core.roles import RoleProfile

# Base band per domain (before the role multiplier).
DOMAIN_BAND: dict[str, float] = {
    "research": 900.0,
    "building_progression": 850.0,
    # Intel events share the MARCH channel with raids + gather. They're quick
    # (the march frees in minutes) and time-limited (the board refreshes on a
    # timer), so they're banded above ordinary raids AND above the boosted-gather
    # ceiling (gather 450 × economy gather_boost 1.6 = 720): clear the quick,
    # expiring Intel run before committing a slot to a long gather.
    "intel": 760.0,
    # Time-limited events that spend a march (e.g. Romance Season: a daily-capped,
    # TTL'd attack). Use-it-or-lose-it like intel, banded just below it and above
    # ordinary raids/gather. One band shared by such events; tune per-event via boost.
    "romance_season": 750.0,
    "raids": 600.0,
    "heroes": 580.0,
    "pets": 560.0,
    "building_camp": 560.0,
    "troops": 540.0,
    "building_economy": 520.0,
    "gather": 450.0,
}
DEFAULT_BAND = 300.0

# Which role category tilts each domain (growth = universal, never demoted).
DOMAIN_CATEGORY: dict[str, str] = {
    "research": "growth",
    "building_progression": "growth",
    "building_economy": "economy",
    "gather": "economy",
    "building_camp": "battle",
    "raids": "battle",
    "troops": "battle",
    "heroes": "growth",          # role tilt already baked into the hero planner's value
    "pets": "growth",            # role tilt already baked into the pet planner's value
    "intel": "growth",           # time-limited free loot — valuable to every role
    "romance_season": "growth",  # time-limited event reward — valuable to every role
}

# Map a building planner track to a coordinator domain.
TRACK_DOMAIN: dict[str, str] = {
    "progression": "building_progression",
    "bottleneck": "building_economy",
    "economy": "building_economy",
    "camp": "building_camp",
}


def domain_priority(
    domain: str,
    role: RoleProfile | None = None,
    rank_nudge: float = 0.0,
    boost: float = 1.0,
) -> float:
    """Cross-domain priority for a domain's action.

    ``rank_nudge`` (small, usually ≤0) preserves intra-domain order when a domain
    offers several candidates for several channels (e.g. queue-1 above queue-2).
    ``boost`` is the calendar/event multiplier (>1 while a points event rewards
    this domain — see :mod:`events`), applied on top of the role tilt.
    """
    band = DOMAIN_BAND.get(domain, DEFAULT_BAND)
    if role is not None:
        band *= role_multiplier(role, DOMAIN_CATEGORY.get(domain, "growth"))
    return band * boost + rank_nudge
