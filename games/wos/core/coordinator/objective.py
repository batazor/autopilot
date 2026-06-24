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

from .domains import domain_bands, domain_categories

if TYPE_CHECKING:
    from games.wos.core.roles import RoleProfile

# Built from the single source of truth (:mod:`domains`). Add/retune a domain there.
DOMAIN_BAND: dict[str, float] = domain_bands()
DEFAULT_BAND = 300.0

# Which role category tilts each domain (growth = universal, never demoted).
DOMAIN_CATEGORY: dict[str, str] = domain_categories()

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
