"""Per-account role profiles — what this bot is *for*.

Each account plays a role: a **farm** cares most about resource production, a
**fighter** about army/combat, a **balanced** account splits evenly. A role is a
set of weight multipliers by category (growth / economy / battle) that bias the
value-greedy planners (research today; the resource allocator and a building
tie-break later) toward that account's purpose.

Crucially, **growth stays ×1.0 in every role** — the Growth branch (extra march
queue, research-speed and construction-speed compounding) is universal profit, so
no role de-prioritises it. Roles only trade economy against battle.

Pure data; no IO. The per-player role lives in account config / player state
(reader deferred); planners resolve it via :func:`get_role` and apply
:func:`multiplier`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Weight categories. Every game branch maps to one of these.
GROWTH = "growth"
ECONOMY = "economy"
BATTLE = "battle"


@dataclass(frozen=True, slots=True)
class RoleProfile:
    """A named bias: per-category weight multipliers, plus opt-out buildings."""

    id: str
    label: str
    description: str
    mult: Mapping[str, float]              # category → multiplier (default 1.0 when absent)
    no_build: frozenset[str] = frozenset()  # building spec ids this role never upgrades
    ignore_overflow_cap: bool = False       # keep gathering past the storehouse cap (hoard)


ROLES: dict[str, RoleProfile] = {
    "balanced": RoleProfile(
        "balanced", "Balanced", "Even split — no category favoured.",
        {GROWTH: 1.0, ECONOMY: 1.0, BATTLE: 1.0},
    ),
    # Down-weight only: the favoured category keeps its full meta weight, the
    # other is suppressed. This tilts economy↔battle WITHOUT ever lifting either
    # above Growth — so the universal-profit Growth techs stay on top for all.
    #
    # A farm is the alliance's resource alt: it gathers hard and is *meant* to be
    # plundered by the main. So it never upgrades the Storehouse (``no_build``) —
    # the protected-resource cap stays low, leaving the pile raidable — and it
    # keeps gathering past that small cap (``ignore_overflow_cap``) so the raidable
    # pile grows instead of throttling at the cap. Battle is suppressed (it
    # shouldn't defend), which on the march channel already lifts gather above
    # raids — the "resource production first" tilt with no weight > 1.
    "farm": RoleProfile(
        "farm", "Farm", "Resource gathering first; stays plunderable (no Storehouse).",
        {GROWTH: 1.0, ECONOMY: 1.0, BATTLE: 0.5},
        no_build=frozenset({"storehouse"}),
        ignore_overflow_cap=True,
    ),
    "fighter": RoleProfile(
        "fighter", "Fighter", "Army / combat first; light economy.",
        {GROWTH: 1.0, ECONOMY: 0.5, BATTLE: 1.0},
    ),
}
DEFAULT_ROLE_ID = "balanced"


def branch_category(branch: str) -> str:
    """Map a game branch id to a weight category.

    ``growth`` / ``economy`` map by name; everything else (battle plus the
    T11/T12 troop branches) is combat → ``battle``.
    """
    b = (branch or "").lower()
    if GROWTH in b:
        return GROWTH
    if ECONOMY in b:
        return ECONOMY
    return BATTLE


def get_role(role_id: str | None) -> RoleProfile:
    """Resolve a role id (case-insensitive), falling back to the default."""
    return ROLES.get((role_id or "").strip().lower(), ROLES[DEFAULT_ROLE_ID])


def multiplier(role: RoleProfile, branch: str) -> float:
    """The weight multiplier ``role`` applies to a node in ``branch``."""
    return role.mult.get(branch_category(branch), 1.0)


def blocks_building(role: RoleProfile | None, spec_id: str) -> bool:
    """Whether ``role`` opts out of ever upgrading building ``spec_id``.

    Drives the build planner's hard exclusions (farm → Storehouse). ``None`` role
    blocks nothing.
    """
    return role is not None and spec_id in role.no_build
