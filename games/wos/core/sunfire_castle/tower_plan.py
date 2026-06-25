"""Buff-tower capture planner — which Sunfire Castle buff towers to help capture, by role.

Calculator-only (no coordinator channel): the buff towers are fixed map structures that
don't draw from the shared economy resource pool, so they're ranked, not arbitrated. Given
which towers the alliance already holds and the account's role, rank the uncontrolled towers
by ``value = booster% × role-fit(buff type) × proximity-to-castle`` and return the top picks
plus a per-type summary. Mirrors the koi/svs calculator pattern (pure, frozen-dataclass
results under ``dataclasses.asdict``).

Role tilt follows the shared down-weight-only philosophy (``games/wos/core/roles``): a
fighter keeps weapon/defense at full weight while gathering/production is halved (so combat
towers out-rank economy ones), a farm does the reverse, and the universal Growth towers
(tech/construction/training/expedition) keep full weight for everyone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from games.wos.core.roles import BATTLE, ECONOMY, GROWTH, RoleProfile, get_role
from games.wos.core.sunfire_castle.territory import Territory, Tower, load_territory

if TYPE_CHECKING:
    from collections.abc import Mapping

# Buff type → role-weight category. tech (Research), construction, training and
# expedition (march speed) are universal Growth; weapon/defense are combat;
# gathering/production are economy.
BUFF_CATEGORY: dict[str, str] = {
    "tech": GROWTH,
    "construction": GROWTH,
    "training": GROWTH,
    "expedition": GROWTH,
    "weapon": BATTLE,
    "defense": BATTLE,
    "gathering": ECONOMY,
    "production": ECONOMY,
}

# How much proximity to the castle lifts value: a central tower (dist 0) gets
# ×(1 + W), a map-edge tower ×1. Mild — buff% and role dominate; this only breaks
# ties toward the more strategic, central towers.
PROXIMITY_WEIGHT = 0.25


@dataclass(frozen=True, slots=True)
class TowerCandidate:
    """One ranked uncontrolled tower, with its value and why."""

    tower_id: str
    buff_type: str
    label: str
    bonus: str
    level: int
    booster: str
    booster_pct: float
    category: str
    col: int
    row: int
    dist_from_castle: float
    value: float
    role_mult: float


@dataclass(frozen=True, slots=True)
class TowerRanking:
    """Result of ranking the buff towers for a role."""

    role: str
    picks: tuple[TowerCandidate, ...]
    by_type: Mapping[str, int]  # buff_type → count among the picks
    total_towers: int
    controlled: int
    available: int


def buff_category(buff_type: str) -> str:
    """The role-weight category for a buff type (unknown → Growth)."""
    return BUFF_CATEGORY.get(buff_type, GROWTH)


def tower_value(tower: Tower, role: RoleProfile, *, max_dist: float) -> tuple[float, float]:
    """``(value, role_mult)`` for one tower: booster% × role-fit × proximity."""
    role_mult = role.mult.get(buff_category(tower.buff_type), 1.0)
    prox = 1.0
    if max_dist > 0:
        prox = 1.0 + (1.0 - tower.dist_from_castle / max_dist) * PROXIMITY_WEIGHT
    return tower.booster_pct * role_mult * prox, role_mult


def rank_towers(
    controlled: Mapping[str, bool] | None = None,
    role: str | RoleProfile | None = None,
    target_count: int = 5,
    territory: Territory | None = None,
) -> TowerRanking:
    """Rank uncontrolled buff towers by value for ``role``; top ``target_count`` (≤0 = all).

    ``controlled`` maps ``tower_id`` → held? (truthy entries are excluded from the picks).
    """
    t = territory or load_territory()
    rp = role if isinstance(role, RoleProfile) else get_role(role)
    ids = {tw.tower_id for tw in t.towers}
    held = {tid for tid, v in (controlled or {}).items() if v}
    max_dist = max((tw.dist_from_castle for tw in t.towers), default=0.0)

    cands: list[TowerCandidate] = []
    for tw in t.towers:
        if tw.tower_id in held:
            continue
        value, role_mult = tower_value(tw, rp, max_dist=max_dist)
        cands.append(
            TowerCandidate(
                tower_id=tw.tower_id,
                buff_type=tw.buff_type,
                label=tw.label,
                bonus=tw.bonus,
                level=tw.level,
                booster=tw.booster,
                booster_pct=tw.booster_pct,
                category=buff_category(tw.buff_type),
                col=tw.col,
                row=tw.row,
                dist_from_castle=tw.dist_from_castle,
                value=round(value, 3),
                role_mult=role_mult,
            )
        )

    # Highest value first; ties broken toward the castle, then by id for determinism.
    cands.sort(key=lambda c: (-c.value, c.dist_from_castle, c.tower_id))
    n = int(target_count)
    picks = tuple(cands) if n <= 0 else tuple(cands[:n])

    by_type: dict[str, int] = {}
    for c in picks:
        by_type[c.buff_type] = by_type.get(c.buff_type, 0) + 1

    return TowerRanking(
        role=rp.id,
        picks=picks,
        by_type=dict(sorted(by_type.items())),
        total_towers=len(t.towers),
        controlled=len(held & ids),
        available=len(cands),
    )
