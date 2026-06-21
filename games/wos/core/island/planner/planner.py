"""Daybreak Island planner: decide which island thing to build/upgrade next.

Pure decision function over :class:`~model.IslandData` plus the player's live
island state. The **Tree of Life** is the spearhead (the island's Furnace): we
always want to push it one level. But its next level is gated by a **Prosperity**
threshold *and* a **Life Essence** cost, and Prosperity comes from decorations —
so when the tree can't advance, the planner pivots:

* tree **ready + affordable**  → upgrade the Tree of Life (the spine pick).
* tree **prosperity-blocked**  → build the most valuable *prosperity-efficient*
  decoration (role-tilted) to climb toward the threshold — the island analog of
  the build planner's bottleneck repair.
* tree **blocked on Life Essence only** → lean on a Life-Essence producer (Lumber
  Camp) / let the economy catch up; meanwhile build any affordable buff that helps.

Among everything available the highest-value affordable candidate wins, exactly
like the other value-greedy planners. Live readers (tree level, prosperity, LE
balance, owned decoration levels, furnace level) are deferred — this module only
answers "what next?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from games.wos.core.roles import ECONOMY
from games.wos.core.roles import multiplier as role_multiplier

from . import policy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from games.wos.core.roles import RoleProfile

    from .model import IslandData

# --- Plan reasons ------------------------------------------------------------
SELECTED = "selected"                      # a candidate was picked
INSUFFICIENT_LIFE_ESSENCE = "insufficient_life_essence"   # candidates exist, none affordable
ALL_MAXED = "all_maxed"                    # nothing left to build

# Candidate kinds.
TREE = "tree_of_life"
DECORATION = "decoration"
PRODUCER = "lumber_camp"
LIGHTHOUSE = "starry_lighthouse"


@dataclass(frozen=True, slots=True)
class IslandState:
    """The player's live Daybreak Island state (reader deferred)."""

    tree_of_life_level: int = 1
    prosperity: int = 0
    life_essence: int = 0
    decorations: Mapping[str, int] = field(default_factory=dict)   # id → current level
    lumber_camp_levels: Sequence[int] = ()                         # per-plot levels
    furnace_level: int = 0                                         # main-city furnace
    trees_cleared: bool = False

    def deco_level(self, deco_id: str) -> int:
        return int(self.decorations.get(deco_id, 0) or 0)


@dataclass(frozen=True, slots=True)
class IslandCandidate:
    """One buildable island action with its value and affordability."""

    kind: str                 # TREE | DECORATION | PRODUCER | LIGHTHOUSE
    target_id: str
    label: str
    to_level: int
    value: float
    life_essence_cost: int
    affordable: bool
    prosperity_gain: int = 0


@dataclass(frozen=True, slots=True)
class IslandPlan:
    """What to do on the island this pass, plus the ranked trace + tree status."""

    pick: IslandCandidate | None
    reason: str
    candidates: tuple[IslandCandidate, ...] = ()
    tree_prosperity_blocked: bool = False
    prosperity_shortfall: int = 0          # prosperity still needed for the next tree level
    life_essence_shortfall: int = 0        # LE still needed for the *picked* action


def _tree_candidate(
    data: IslandData, state: IslandState
) -> tuple[IslandCandidate | None, bool, int]:
    """The Tree-of-Life upgrade candidate (or None if maxed), plus whether it's
    prosperity-blocked and by how much."""
    nxt = data.tree_level(state.tree_of_life_level + 1)
    if nxt is None:
        return None, False, 0
    prosperity_gap = max(0, nxt.prosperity_required - state.prosperity)
    blocked = prosperity_gap > 0
    le_ok = state.life_essence >= nxt.life_essence
    cand = IslandCandidate(
        kind=TREE,
        target_id="tree_of_life",
        label=f"Tree of Life → Lv.{nxt.level}",
        to_level=nxt.level,
        value=policy.TREE_WEIGHT,
        life_essence_cost=nxt.life_essence,
        affordable=(not blocked) and le_ok,
    )
    return cand, blocked, prosperity_gap


def _decoration_candidates(
    data: IslandData, state: IslandState, role: RoleProfile | None, *, blocked: bool
) -> list[IslandCandidate]:
    """One candidate per decoration that isn't maxed (build a new one / level up)."""
    out: list[IslandCandidate] = []
    pool = (*data.decorations, *data.fillers) if blocked else data.decorations
    for deco in pool:
        cur = state.deco_level(deco.id)
        if cur >= deco.max_level:
            continue
        value = policy.decoration_value(deco, role, prosperity_blocked=blocked)
        if value <= 0:
            continue                                   # no-buff filler, tree not blocked → skip
        out.append(
            IslandCandidate(
                kind=DECORATION,
                target_id=deco.id,
                label=f"{deco.name} → Lv.{cur + 1}",
                to_level=cur + 1,
                value=value,
                life_essence_cost=deco.life_essence,
                affordable=state.life_essence >= deco.life_essence,
                prosperity_gain=deco.prosperity,
            )
        )
    return out


def _producer_candidate(
    data: IslandData, state: IslandState, role: RoleProfile | None
) -> IslandCandidate | None:
    """Next Lumber Camp upgrade (more Life Essence/hour), if a plot is below max
    and the main-city Furnace gate is met. Cost data isn't published → treated as
    affordable; it competes on value as the LE-throughput investment."""
    s = data.structure("lumber_camp")
    if s is None:
        return None
    gate = (s.unlock or {})
    if gate.get("building") == "furnace" and state.furnace_level < int(gate.get("level", 0)):
        return None                                    # locked: furnace too low
    cap = s.max_level or 0
    plots = list(state.lumber_camp_levels) or [0] * max(1, s.plots)
    lowest = min(plots) if plots else 0
    if cap and lowest >= cap:
        return None                                    # every plot maxed
    value = policy.PRODUCER_WEIGHT * (role_multiplier(role, ECONOMY) if role else 1.0)
    return IslandCandidate(
        kind=PRODUCER, target_id="lumber_camp", label=f"Lumber Camp → Lv.{lowest + 1}",
        to_level=lowest + 1, value=value, life_essence_cost=0, affordable=True,
    )


def _lighthouse_candidate(
    data: IslandData, state: IslandState
) -> IslandCandidate | None:
    """Starry Lighthouse blueprint — only once the Tree of Life hits its max level
    and it isn't built yet."""
    s = data.structure("starry_lighthouse")
    if s is None or state.deco_level("starry_lighthouse") > 0:
        return None
    gate = (s.unlock or {})
    if gate.get("building") == "tree_of_life" and state.tree_of_life_level < int(gate.get("level", 0)):
        return None
    return IslandCandidate(
        kind=LIGHTHOUSE, target_id="starry_lighthouse", label="Starry Lighthouse (blueprint)",
        to_level=1, value=policy.RARITY_BUFF_WEIGHT["mythic"], life_essence_cost=s.life_essence,
        affordable=state.life_essence >= s.life_essence, prosperity_gain=s.prosperity,
    )


def plan_island_next(
    data: IslandData,
    state: IslandState,
    *,
    role: RoleProfile | None = None,
) -> IslandPlan:
    """Pick the single best island action under the Tree-of-Life-first policy.

    Value-greedy + role-biased: the Tree-of-Life upgrade (the spine) competes with
    decorations (buff value, plus a Prosperity premium while the tree is blocked),
    a Lumber-Camp producer, and the Starry Lighthouse. The highest-value
    *affordable* candidate wins; when the tree is prosperity-blocked the premium
    lifts prosperity-efficient decorations so the planner climbs to the threshold.
    """
    tree, blocked, prosperity_gap = _tree_candidate(data, state)

    candidates: list[IslandCandidate] = []
    if tree is not None:
        candidates.append(tree)
    candidates.extend(_decoration_candidates(data, state, role, blocked=blocked))
    prod = _producer_candidate(data, state, role)
    if prod is not None:
        candidates.append(prod)
    light = _lighthouse_candidate(data, state)
    if light is not None:
        candidates.append(light)

    # Role opt-outs (island ids won't collide with main-city no_build, but stay
    # consistent with the other planners).
    if role is not None and role.no_build:
        candidates = [c for c in candidates if c.target_id not in role.no_build]

    ranked = tuple(
        sorted(candidates, key=lambda c: (-c.value, c.life_essence_cost, c.target_id))
    )
    affordable = [c for c in ranked if c.affordable]
    pick = affordable[0] if affordable else None

    if pick is not None:
        reason = SELECTED
    elif ranked:
        reason = INSUFFICIENT_LIFE_ESSENCE
    else:
        reason = ALL_MAXED

    le_short = 0
    if pick is not None:
        le_short = max(0, pick.life_essence_cost - state.life_essence)
    elif ranked:
        le_short = max(0, ranked[0].life_essence_cost - state.life_essence)

    return IslandPlan(
        pick=pick,
        reason=reason,
        candidates=ranked,
        tree_prosperity_blocked=blocked,
        prosperity_shortfall=prosperity_gap,
        life_essence_shortfall=le_short,
    )
