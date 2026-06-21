"""Pure participant selection for cross-account campaigns.

Picks who plays each campaign from a list of candidate accounts (built by the
adapter from the fleet snapshot + per-account opt-in flags + roles), by role +
opt-in + alliance + (for joint events) the calendar window. No IO.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from coord.campaign import Participant

if TYPE_CHECKING:
    from collections.abc import Sequence

# raid_role values
RAID_OFF = "off"
RAID_FARM = "farm"
RAID_FIGHTER = "fighter"


@dataclass(frozen=True, slots=True)
class Candidate:
    """An account the orchestrator could enlist (one row of the fleet snapshot)."""

    fid: str
    instance_id: str
    online: bool
    alliance: str
    role: str               # economy role (farm/fighter/balanced)
    raid_role: str = RAID_OFF
    events_opt_in: bool = False
    reinforce_opt_in: bool = False


def _mark_shared(rows: list[tuple[Candidate, str]]) -> list[Participant]:
    """Build Participants, flagging shares_device when >1 land on one instance."""
    counts = Counter(c.instance_id for c, _ in rows)
    return [
        Participant(
            fid=c.fid,
            role=role,
            instance_id=c.instance_id,
            shares_device=counts[c.instance_id] > 1,
        )
        for c, role in rows
    ]


def _dominant_alliance(cands: Sequence[Candidate]) -> str:
    tags = Counter(c.alliance for c in cands if c.alliance)
    return tags.most_common(1)[0][0] if tags else ""


def select_joint_event(
    candidates: Sequence[Candidate], *, max_n: int | None = None
) -> list[Participant]:
    """Online, opted-in accounts of the single largest participating alliance."""
    pool = [c for c in candidates if c.online and c.events_opt_in and c.alliance]
    if not pool:
        return []
    alliance = _dominant_alliance(pool)
    chosen = [c for c in pool if c.alliance == alliance]
    if max_n is not None:
        chosen = chosen[:max_n]
    return _mark_shared([(c, c.role) for c in chosen])


def select_farm_raid(
    candidates: Sequence[Candidate], *, alliance: str | None = None
) -> list[Participant]:
    """Pair one farm with one fighter (same alliance). Empty if no valid pair."""
    farms = [c for c in candidates if c.online and c.raid_role == RAID_FARM]
    fighters = [c for c in candidates if c.online and c.raid_role == RAID_FIGHTER]
    for farm in farms:
        for fighter in fighters:
            if fighter.fid == farm.fid:
                continue
            if alliance is not None and not (farm.alliance == fighter.alliance == alliance):
                continue
            if alliance is None and farm.alliance != fighter.alliance:
                continue
            return _mark_shared([(farm, RAID_FARM), (fighter, RAID_FIGHTER)])
    return []


def select_reinforcement(
    candidates: Sequence[Candidate],
    victim_fid: str,
    *,
    alliance: str | None = None,
    max_n: int = 2,
) -> list[Participant]:
    """Opted-in allied helpers (excluding the victim) to send reinforcements."""
    target_alliance = alliance
    if target_alliance is None:
        for c in candidates:
            if c.fid == victim_fid:
                target_alliance = c.alliance
                break
    helpers = [
        c
        for c in candidates
        if c.online
        and c.reinforce_opt_in
        and c.fid != victim_fid
        and (target_alliance is None or c.alliance == target_alliance)
    ]
    return _mark_shared([(c, "helper") for c in helpers[:max_n]])


def select_participants(
    campaign_id: str,
    candidates: Sequence[Candidate],
    *,
    victim_fid: str | None = None,
    max_n: int | None = None,
) -> list[Participant]:
    """Dispatch to the per-campaign selector by id."""
    if campaign_id == "joint_event":
        return select_joint_event(candidates, max_n=max_n)
    if campaign_id == "farm_raid":
        return select_farm_raid(candidates)
    if campaign_id == "reinforcement":
        return select_reinforcement(candidates, victim_fid or "")
    return []
