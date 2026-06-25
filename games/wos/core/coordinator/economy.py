"""Closed economy loop — keep production ahead of consumption.

The coordinator reports which resources blocked a valuable action
(``bottleneck_resources``). This layer turns that (plus low-buffer / near-cap
signals) into two corrective biases the coordinator then acts on:

1. **Gather the short resource** — emit a targeted gather candidate on a march
   channel, lifted above ordinary raids so marches go fetch what's scarce.
2. **Upgrade its producer** — boost the building-economy candidate for that
   resource's producer (coal short → coal_mine), raising passive output.

And the inverse: a resource near its storehouse cap is flagged ``overflow`` —
don't gather it (it would burn), spend it down instead. Pure; consumes balances +
the coordinator's bottleneck signal, returns biases. Resource keys are canonical
names (meat/wood/coal/iron) shared with research costs and the producer map below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .model import MARCH, CandidateAction, Utility
from .objective import domain_priority

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from games.wos.core.roles import RoleProfile

# Which producer building makes each base resource (canonical names → spec id).
PRODUCER_BY_RESOURCE: dict[str, str] = {
    "meat": "hunters_hut",
    "wood": "sawmill",
    "coal": "coal_mine",
    "iron": "iron_mine",
}

DEFAULT_GATHER_BOOST = 1.6        # lift the gather domain above ordinary raids
DEFAULT_PRODUCER_BOOST = 1.4      # lift the short resource's producer upgrade
DEFAULT_OVERFLOW_FRACTION = 0.9   # ≥ this share of cap → near overflow


@dataclass(frozen=True, slots=True)
class EconomyBias:
    """Corrective biases derived from the resource balance + bottleneck."""

    short_resources: tuple[str, ...]                  # produce more of these
    gather_boost: float                                # multiplier for gather domain
    gather_targets: tuple[str, ...]                    # resources to gather, neediest first
    producer_boost: Mapping[str, float] = field(default_factory=dict)  # producer id → mult
    overflow_resources: tuple[str, ...] = ()           # near cap → spend, don't gather


def economy_bias(
    balances: Mapping[str, int],
    *,
    bottleneck: Sequence[str] = (),
    caps: Mapping[str, int] | None = None,
    min_buffer: Mapping[str, int] | None = None,
    role: RoleProfile | None = None,
    gather_boost: float = DEFAULT_GATHER_BOOST,
    producer_boost: float = DEFAULT_PRODUCER_BOOST,
    overflow_fraction: float = DEFAULT_OVERFLOW_FRACTION,
) -> EconomyBias:
    """Decide what to produce more of (and what to stop producing).

    ``bottleneck`` = resources that blocked a valuable action this tick (reactive).
    ``min_buffer`` = a proactive floor per resource (top up before it bites).
    ``caps`` = storehouse capacities for overflow detection.

    A hoard role (farm — ``role.ignore_overflow_cap``) skips overflow detection
    entirely: its Storehouse cap is deliberately tiny so the pile stays raidable,
    so the cap must NOT throttle gathering (otherwise the small cap would suppress
    every gather). It keeps piling resources up unprotected instead.
    """
    short: set[str] = set(bottleneck)
    if min_buffer:
        for r, buf in min_buffer.items():
            if int(balances.get(r, 0)) < buf:
                short.add(r)

    overflow: list[str] = []
    hoard = role is not None and role.ignore_overflow_cap
    if caps and not hoard:
        for r, cap in caps.items():
            if cap > 0 and int(balances.get(r, 0)) >= overflow_fraction * cap:
                overflow.append(r)

    # Don't gather what's about to overflow; gather the most-depleted first.
    targets = sorted((r for r in short if r not in overflow), key=lambda r: int(balances.get(r, 0)))
    pboost = {
        PRODUCER_BY_RESOURCE[r]: producer_boost
        for r in targets
        if r in PRODUCER_BY_RESOURCE
    }
    return EconomyBias(
        short_resources=tuple(sorted(short)),
        gather_boost=gather_boost if targets else 1.0,
        gather_targets=tuple(targets),
        producer_boost=pboost,
        overflow_resources=tuple(sorted(overflow)),
    )


def gather_candidates(
    bias: EconomyBias,
    *,
    role: RoleProfile | None = None,
) -> list[CandidateAction]:
    """March-channel gather candidates for the short resources (neediest first).

    Gathering costs no shared resources (it produces them), so ``cost`` is empty;
    the gather-domain boost is what lifts these above ordinary raids when scarce.
    """
    out: list[CandidateAction] = []
    for i, resource in enumerate(bias.gather_targets):
        out.append(CandidateAction(
            domain="gather",
            channel_kind=MARCH,
            key=f"gather_{resource}",
            utility=Utility(
                base_value=domain_priority("gather", role, rank_nudge=-float(i), boost=bias.gather_boost)
            ),
            cost={},
            detail=f"gather {resource}",
        ))
    return out
