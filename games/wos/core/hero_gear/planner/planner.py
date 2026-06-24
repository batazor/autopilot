"""Value-greedy Hero Gear planner: which (piece, track) to upgrade next.

Pure decision over the 3 upgrade tracks (enhance / mastery / widget) × 6 pieces, the
per-piece per-track levels owned, and current material balances. Each track is gated
by its own Furnace unlock. Ranks every (piece, track) next step by value (track weight
× composition × role × normalised even-leveling) and picks the best affordable one.
:func:`hero_gear_roadmap` totals materials to bring everything to per-track targets —
the calculator's headline. Live readers (per-piece per-track levels) are deferred.

Resource keys: ``enhancement_xp`` / ``essence_stones`` / ``weapon_widget`` (one per
track; match db/items where present).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .model import load_hero_gear_data
from .policy import hero_gear_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import HeroGearData

# Plan reasons
SELECTED = "selected"
LOCKED = "locked"                       # no track unlocked yet (Furnace too low)
INSUFFICIENT_RESOURCES = "insufficient_resources"
NONE = "none"                           # every unlocked track already at its cap


@dataclass(frozen=True, slots=True)
class HeroGearCandidate:
    slot_id: str              # "gloves_belt_infantry" … "goggles_boots_marksman"
    troop_type: str           # infantry | lancer | marksman
    track: str                # enhance | mastery | widget
    to_level: int
    value: float
    cost: Mapping[str, int]   # {track resource: amount}
    affordable: bool


@dataclass(frozen=True, slots=True)
class HeroGearPlan:
    step: HeroGearCandidate | None
    reason: str
    candidates: tuple[HeroGearCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class HeroGearRoadmap:
    """Totals to bring every piece's tracks to their targets (the calculator's output)."""

    cost: Mapping[str, int]   # total materials, per resource
    steps: int                # number of upgrade steps


def _level(owned: Mapping[str, Mapping[str, int]], slot_id: str, track: str) -> int:
    entry = owned.get(slot_id) or {}
    try:
        return int(entry.get(track, 0) or 0)
    except (TypeError, ValueError):
        return 0


def plan_next(
    owned: Mapping[str, Mapping[str, int]],
    resources: Mapping[str, int],
    *,
    furnace_level: int | None = None,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
    data: HeroGearData | None = None,
) -> HeroGearPlan:
    """Pick the next hero-gear upgrade across all (piece, track) steps, within budget.

    ``owned`` maps ``slot_id`` → ``{track: current_level}`` (missing = level 0). Each
    track is gated by its own ``unlock_furnace_level``; pass ``furnace_level=None`` to
    skip the gates.
    """
    d = data if data is not None else load_hero_gear_data()

    candidates: list[HeroGearCandidate] = []
    best: HeroGearCandidate | None = None
    best_key: tuple[float, int] | None = None
    any_unlocked = False
    any_upgradable = False

    for slot_id, troop_type in d.pieces.items():
        for track_name, track in d.tracks.items():
            if furnace_level is not None and furnace_level < track.unlock_furnace_level:
                continue                               # track gated by Furnace
            any_unlocked = True
            cur = _level(owned, slot_id, track_name)
            if cur >= track.max_level:
                continue
            nxt = cur + 1
            amt = track.cost_at(nxt)
            if amt is None:
                continue
            any_upgradable = True
            cost = {track.resource: amt}
            value = hero_gear_value(troop_type, track_name, nxt,
                                    max_level=track.max_level, role=role, target=target)
            affordable = int(resources.get(track.resource, 0)) >= amt
            cand = HeroGearCandidate(
                slot_id=slot_id, troop_type=troop_type, track=track_name,
                to_level=nxt, value=value, cost=cost, affordable=affordable,
            )
            candidates.append(cand)
            if affordable:
                key = (value, -amt)
                if best_key is None or key > best_key:
                    best, best_key = cand, key

    candidates.sort(key=lambda c: (-c.value, c.slot_id, c.track))
    if best is not None:
        reason = SELECTED
    elif not any_unlocked:
        reason = LOCKED
    elif any_upgradable:
        reason = INSUFFICIENT_RESOURCES
    else:
        reason = NONE
    return HeroGearPlan(step=best, reason=reason, candidates=tuple(candidates[:8]))


def hero_gear_roadmap(
    owned: Mapping[str, Mapping[str, int]],
    targets: Mapping[str, int],
    *,
    data: HeroGearData | None = None,
) -> HeroGearRoadmap:
    """Total materials + step count to bring every piece's tracks to ``targets``.

    ``targets`` maps ``track → target_level``; a track absent from ``targets`` is skipped.
    """
    d = data if data is not None else load_hero_gear_data()
    cost: dict[str, int] = {}
    steps = 0
    for slot_id in d.pieces:
        for track_name, track in d.tracks.items():
            tgt = targets.get(track_name)
            if tgt is None:
                continue
            cap = min(int(tgt), track.max_level)
            cur = _level(owned, slot_id, track_name)
            for n in range(cur + 1, cap + 1):
                amt = track.cost_at(n)
                if amt is None:
                    continue
                cost[track.resource] = cost.get(track.resource, 0) + amt
                steps += 1
    return HeroGearRoadmap(cost=cost, steps=steps)
