"""Data model for the cross-domain coordinator — the bot's "brain".

The domain planners (building, research, raids/resource-world, troops) each
decide *what's best in their own domain*. But they all draw on one shared pool of
base resources and run on a handful of parallel execution **channels**
(construction queues, the research queue, march slots, training queues). Left
alone they'd double-spend the same wood and optimise locally.

The coordinator sits above them: every planner emits :class:`CandidateAction`s
(tagged with the channel kind they need, a cross-domain ``priority``, and the
shared ``cost`` they'd consume); :func:`coordinator.coordinate` fills each idle
channel with the highest-priority action that still fits the shared budget, and
reports what got starved (the global bottleneck) so the economy loop can react.

Pure data — no IO. ``priority`` is on one common scale across domains (assigned by
:mod:`objective` from the account role); ``cost`` keys are canonical resource ids
(meat / wood / coal / iron / …) shared by every domain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- Execution channel kinds (parallel lanes that can each run one task) ------
CONSTRUCTION = "construction"
RESEARCH = "research"
MARCH = "march"
TRAINING = "training"
HERO = "hero"                # hero investment (books/shards spend)
PET = "pet"                  # pet investment (food/shards spend)
CHARM = "charm"              # Chief Charm investment (guide/design/secret spend)
GEAR = "gear"                # Chief Gear investment (alloy/polishing/design/amber spend)
HERO_GEAR = "hero_gear"      # Hero Gear investment (enhance/mastery/widget spend)
VIP = "vip"                  # VIP progression (vip_points spend → VIP level)


@dataclass(frozen=True, slots=True)
class Channel:
    """One idle execution lane the coordinator can fill this tick."""

    id: str          # stable lane id ("construction_1", "march_2")
    kind: str        # CONSTRUCTION | RESEARCH | MARCH | TRAINING


# --- Utility component weights ------------------------------------------------
# How much each component contributes to ``Utility.total``. Only ``base_value`` is
# active in this iteration; the rest are *wired* (planners populate the raw measures
# so they show up in the decision trace for explainability) but weighted 0 until
# calibrated — see the coordinator plan's steps 3–5 (shadow price / MPC / WAIT).
# Flipping a weight here is the single knob that turns a component on.
W_EVENT_POINTS = 0.0
W_UNLOCK = 0.0
W_RESOURCE_ROI = 0.0
W_TIME = 0.0
W_OPPORTUNITY = 0.0
W_RISK = 0.0
W_SHADOW = 0.0


@dataclass(frozen=True, slots=True)
class Utility:
    """A candidate's value decomposed into components on the common scale.

    ``base_value`` is the always-on cross-domain term (domain band × role × event
    boost, plus the planner's normalised intra-domain value — see
    :func:`objective.domain_priority` / :mod:`adapters`). The remaining fields hold
    **raw measures** (event-point count, seconds, scarcity, …) that ``total`` folds
    in via the module-level ``W_*`` weights; with the current weights only
    ``base_value`` moves the result, so the breakdown is fully informative while the
    allocator's behaviour stays exactly today's + the new alternatives.

    ``weight`` is a post-hoc multiplier the feedback layer uses to back off a stuck
    action (it scales the whole utility, not a single component).
    """

    base_value: float = 0.0
    event_points: float = 0.0      # raw event-points an action nets in an active window
    unlock_value: float = 0.0      # raw downstream value this unlocks (reserved)
    resource_roi: float = 0.0      # raw resource income the action gains (reserved)
    time_cost: float = 0.0         # raw seconds the action occupies its channel (reserved)
    opportunity_cost: float = 0.0  # reserved (MPC lookahead)
    risk_penalty: float = 0.0      # reserved (a-priori failure estimate)
    shadow_cost: float = 0.0       # raw resource scarcity penalty (reserved, step 3)
    weight: float = 1.0            # feedback backoff multiplier (≤1 while stuck)

    @property
    def total(self) -> float:
        raw = (
            self.base_value
            + W_EVENT_POINTS * self.event_points
            + W_UNLOCK * self.unlock_value
            + W_RESOURCE_ROI * self.resource_roi
            - W_TIME * self.time_cost
            - W_OPPORTUNITY * self.opportunity_cost
            - W_RISK * self.risk_penalty
            - W_SHADOW * self.shadow_cost
        )
        return raw * self.weight


@dataclass(frozen=True, slots=True)
class CandidateAction:
    """A domain planner's proposed action, on the common priority scale."""

    domain: str                # building_progression | research | raids | gather | …
    channel_kind: str          # which channel kind it needs
    key: str                   # action id (for the trace / dispatch)
    utility: Utility           # CROSS-DOMAIN value breakdown (assigned by objective + adapters)
    cost: Mapping[str, int] = field(default_factory=dict)   # shared resources spent
    detail: str = ""

    @property
    def priority(self) -> float:
        """The scalar the allocator sorts/sums on — the utility's total."""
        return self.utility.total


@dataclass(frozen=True, slots=True)
class Commit:
    """A candidate assigned to a concrete channel this tick."""

    channel_id: str
    action: CandidateAction


@dataclass(frozen=True, slots=True)
class CoordinatorDecision:
    """The tick's plan: what runs where, plus why the rest didn't."""

    commits: tuple[Commit, ...]
    starved: tuple[CandidateAction, ...]      # had a free channel, blocked on resources
    no_channel: tuple[CandidateAction, ...]   # no idle channel of their kind
    remaining: Mapping[str, int]              # resource balances after commits
    bottleneck_resources: tuple[str, ...]     # resources that blocked something

    def committed_for(self, channel_kind: str) -> tuple[Commit, ...]:
        return tuple(c for c in self.commits if c.action.channel_kind == channel_kind)
