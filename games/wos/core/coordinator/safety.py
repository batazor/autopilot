"""Situational safety — the reactive override on top of the proactive planners.

Calendar/economy *bias* priorities; safety is different — it **gates and
preempts**. When the city is in danger (an incoming attack, or a live PvP window
like SvS/KE), the smart move is defensive:

* **Suppress** the domains that send troops outside the walls (gathering, raids) —
  troops on the map get killed during PvP. These are blocked, not just lowered.
* **Inject** mandatory defensive actions — raise a peace shield (or refresh one
  about to expire in a danger window), recall exposed marches, heal the wounded.

Defensive actions are immediate taps, not queue-bound, so they're returned as a
separate ordered list the executor runs first — they don't compete for the
construction/research/march channels. Pure: consumes a parsed :class:`ThreatState`
(from readers; ``pvp_window`` can come straight from the calendar's SvS/state_of_power
flag) and returns a :class:`SafetyDirective`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .model import CandidateAction

# Defensive action kinds (immediate taps).
SHIELD_UP = "shield_up"
RECALL_MARCHES = "recall_marches"
HEAL_INJURED = "heal_injured"

# Domains that put troops outside the city → unsafe during danger.
EXPOSING_DOMAINS: tuple[str, ...] = ("gather", "raids")

DEFAULT_SHIELD_FLOOR_S = 3_600        # refresh a shield with < 1h left during danger


@dataclass(frozen=True, slots=True)
class ThreatState:
    """Parsed danger signals for one player (from readers / the calendar)."""

    incoming_attack: bool = False
    attack_eta_s: float = 0.0
    shield_active: bool = False
    shield_remaining_s: float = 0.0
    pvp_window: bool = False           # SvS / KE live (can come from the calendar)
    troops_exposed: bool = False       # gathering / rally marches are out
    gatherers_under_attack: bool = False   # someone is hitting our gather node(s)
    injured: int = 0                   # wounded troops awaiting heal


@dataclass(frozen=True, slots=True)
class DefensiveAction:
    """An immediate protective tap (not channel-allocated)."""

    kind: str
    reason: str
    urgency: float = 0.0               # higher runs first
    target: str = ""                   # optional scope, e.g. "gather" (recall gatherers)


@dataclass(frozen=True, slots=True)
class SafetyDirective:
    """The safety override for this tick."""

    safe_mode: bool                            # in danger → be defensive
    suppress_domains: tuple[str, ...] = ()     # block these from the channel plan
    actions: tuple[DefensiveAction, ...] = ()  # run these now, urgency-first
    reason: str = ""


def assess_safety(
    threat: ThreatState,
    *,
    shield_floor_s: float = DEFAULT_SHIELD_FLOOR_S,
    exposing_domains: Sequence[str] = EXPOSING_DOMAINS,
) -> SafetyDirective:
    """Turn a threat state into suppressions + immediate defensive actions."""
    actions: list[DefensiveAction] = []
    suppress: set[str] = set()
    reasons: list[str] = []

    danger = bool(threat.incoming_attack or threat.pvp_window)
    if danger:
        if not threat.shield_active:
            actions.append(DefensiveAction(SHIELD_UP, "in danger with no shield", 100.0))
            reasons.append("raise shield")
        elif threat.shield_remaining_s < shield_floor_s:
            actions.append(DefensiveAction(SHIELD_UP, "shield expiring during danger", 80.0))
            reasons.append("refresh shield")
        suppress.update(exposing_domains)      # don't send troops out
        reasons.append("hold gather/raids")

    if threat.incoming_attack and threat.troops_exposed:
        actions.append(DefensiveAction(RECALL_MARCHES, "incoming attack, troops on the map", 90.0))
        reasons.append("recall marches")

    # Gatherers hit on the map: a shield can't save troops outside the walls — the
    # only defence is to recall them, and stop feeding new gathers into the fight.
    if threat.gatherers_under_attack:
        actions.append(DefensiveAction(
            RECALL_MARCHES, "gatherers under attack — recall them", 95.0, target="gather"))
        suppress.add("gather")
        reasons.append("recall gatherers")

    if threat.injured > 0:
        actions.append(DefensiveAction(HEAL_INJURED, f"{threat.injured} wounded", 30.0))
        reasons.append("heal")

    actions.sort(key=lambda a: -a.urgency)
    return SafetyDirective(
        safe_mode=danger or threat.gatherers_under_attack,
        suppress_domains=tuple(sorted(suppress)),
        actions=tuple(actions),
        reason="; ".join(reasons),
    )


def apply_safety(
    candidates: Sequence[CandidateAction],
    directive: SafetyDirective,
) -> list[CandidateAction]:
    """Drop channel candidates whose domain the directive suppresses."""
    if not directive.suppress_domains:
        return list(candidates)
    blocked = set(directive.suppress_domains)
    return [c for c in candidates if c.domain not in blocked]
