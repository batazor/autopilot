"""Safety / mutual-exclusion layer — keep campaigns from shooting each other.

Pure constraint checks the orchestrator applies before arbitration:
* **War/hunt suppression** — while an alliance-war or bear-hunt window is open,
  suppress campaigns that send troops *out* of the city (raids), keeping them
  home for defense. Reinforcement is *defensive*, so it is NOT suppressed.
* **Event exclusivity** — don't raid an account that's a participant in an active
  joint-event (it needs its troops/resources there).
* **Overlap** (creation-time) — don't enlist an account already committed to
  another run (the arbiter enforces this at dispatch; this stops it earlier).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

# Calendar slugs that mean "keep troops home" while active.
WAR_SLUG = "alliance_war"
HUNT_SLUG = "bear_hunt"


@dataclass(frozen=True, slots=True)
class CampaignSafety:
    """Per-campaign safety profile."""

    sends_troops_out: bool          # marches troops away from the city (raid/gather)
    defensive: bool = False         # helps defense (reinforcement) → never suppressed
    exclusive_with_events: bool = False  # must not touch active-event participants


SAFETY: dict[str, CampaignSafety] = {
    "joint_event": CampaignSafety(sends_troops_out=True),
    "farm_raid": CampaignSafety(sends_troops_out=True, exclusive_with_events=True),
    "reinforcement": CampaignSafety(sends_troops_out=True, defensive=True),
}


@dataclass(frozen=True, slots=True)
class SafetyContext:
    event_fids: frozenset[str] = frozenset()   # accounts in active joint-event runs
    war_active: bool = False
    hunt_active: bool = False


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""


def check_dispatch(
    campaign_id: str,
    participant_fids: Collection[str],
    ctx: SafetyContext,
    *,
    profiles: Mapping[str, CampaignSafety] = SAFETY,
) -> Verdict:
    """Whether a run may be dispatched this tick. Safe to apply every tick to an
    already-active run (war/hunt + event-exclusivity only — no self-overlap)."""
    prof = profiles.get(campaign_id)
    if prof is None:
        return Verdict(True)
    if (ctx.war_active or ctx.hunt_active) and prof.sends_troops_out and not prof.defensive:
        return Verdict(False, "war_hunt_keep_troops_home")
    if prof.exclusive_with_events:
        clash = set(participant_fids) & ctx.event_fids
        if clash:
            return Verdict(False, "participant_in_active_event:" + ",".join(sorted(clash)))
    return Verdict(True)


def would_overlap(participant_fids: Collection[str], committed_fids: Collection[str]) -> bool:
    """Creation-time guard: a candidate shares an account with an already-committed run."""
    return bool(set(participant_fids) & set(committed_fids))


def filter_dispatchable(
    runs: Iterable[tuple[str, Collection[str]]],
    ctx: SafetyContext,
    *,
    profiles: Mapping[str, CampaignSafety] = SAFETY,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split ``(run_id paired with (campaign_id, fids))`` into allowed run_ids and
    ``(run_id, reason)`` for the suppressed ones.

    ``runs`` items are ``(run_id, (campaign_id, participant_fids))``.
    """
    allowed: list[str] = []
    blocked: list[tuple[str, str]] = []
    for run_id, (campaign_id, fids) in runs:
        v = check_dispatch(campaign_id, fids, ctx, profiles=profiles)
        if v.allowed:
            allowed.append(run_id)
        else:
            blocked.append((run_id, v.reason))
    return allowed, blocked
