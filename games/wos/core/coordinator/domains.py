"""Single source of truth for the coordinator's domains.

Every cross-domain fact — priority band, role category, the execution channel it
uses, and (for the development domains) its default ``/full`` lane id — lives in one
:class:`DomainSpec` row here. :mod:`objective` builds ``DOMAIN_BAND`` / ``DOMAIN_CATEGORY``
from it, and the API derives the ``/full`` channel list + the ``/meta`` channel kinds
from it — so adding an investment domain is ONE row (plus its adapter + planner)
instead of edits scattered across objective, model, the API and back.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import (
    CHARM,
    CONSTRUCTION,
    GEAR,
    HERO,
    HERO_GEAR,
    MARCH,
    PET,
    RESEARCH,
    TRAINING,
)


@dataclass(frozen=True, slots=True)
class DomainSpec:
    """One coordinator domain: its band, role tilt, channel, and /full lane (if any)."""

    name: str                          # DOMAIN_BAND / DOMAIN_CATEGORY key
    band: float                        # base priority (before the role multiplier)
    category: str                      # growth (universal) | economy | battle
    channel_kind: str | None = None    # execution channel it competes on
    dev_channel_id: str | None = None  # default single lane id in /planner/full (dev domains)


# Ordered high→low band. Bands/categories are the literal previous values (zero
# behaviour change); the band rationale for the time-limited MARCH events:
# intel/romance sit above raids AND the boosted-gather ceiling (gather 450 × 1.6 =
# 720) so a quick, expiring run is taken before a long gather.
DOMAINS: tuple[DomainSpec, ...] = (
    DomainSpec("research", 900.0, "growth", RESEARCH, "research_1"),
    DomainSpec("building_progression", 850.0, "growth", CONSTRUCTION),
    DomainSpec("intel", 760.0, "growth", MARCH),
    DomainSpec("romance_season", 750.0, "growth", MARCH),
    DomainSpec("raids", 600.0, "battle", MARCH),
    DomainSpec("heroes", 580.0, "growth", HERO, "hero_1"),
    DomainSpec("pets", 560.0, "growth", PET, "pet_1"),
    DomainSpec("building_camp", 560.0, "battle", CONSTRUCTION),
    DomainSpec("gear", 555.0, "growth", GEAR, "gear_1"),
    DomainSpec("charms", 550.0, "growth", CHARM, "charm_1"),
    DomainSpec("hero_gear", 545.0, "growth", HERO_GEAR, "hero_gear_1"),
    DomainSpec("troops", 540.0, "battle", TRAINING, "training_1"),
    DomainSpec("building_economy", 520.0, "economy", CONSTRUCTION),
    DomainSpec("gather", 450.0, "economy", MARCH),
)


def domain_bands() -> dict[str, float]:
    return {d.name: d.band for d in DOMAINS}


def domain_categories() -> dict[str, str]:
    return {d.name: d.category for d in DOMAINS}


def channel_kinds() -> tuple[str, ...]:
    """Every distinct execution channel kind, in registry order (for ``/meta``)."""
    out: list[str] = []
    for d in DOMAINS:
        if d.channel_kind and d.channel_kind not in out:
            out.append(d.channel_kind)
    return tuple(out)


def dev_channels() -> tuple[tuple[str, str], ...]:
    """``(channel_id, channel_kind)`` for the single development lanes ``/full`` opens."""
    return tuple(
        (d.dev_channel_id, d.channel_kind)
        for d in DOMAINS
        if d.dev_channel_id and d.channel_kind
    )


def investment_domain_names() -> tuple[str, ...]:
    """Domains that have their own dev lane — the per-domain investment planners."""
    return tuple(d.name for d in DOMAINS if d.dev_channel_id)
