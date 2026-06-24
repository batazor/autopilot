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


@dataclass(frozen=True, slots=True)
class Channel:
    """One idle execution lane the coordinator can fill this tick."""

    id: str          # stable lane id ("construction_1", "march_2")
    kind: str        # CONSTRUCTION | RESEARCH | MARCH | TRAINING


@dataclass(frozen=True, slots=True)
class CandidateAction:
    """A domain planner's proposed action, on the common priority scale."""

    domain: str                # building_progression | research | raids | gather | …
    channel_kind: str          # which channel kind it needs
    key: str                   # action id (for the trace / dispatch)
    priority: float            # CROSS-DOMAIN scale (assigned by objective)
    cost: Mapping[str, int] = field(default_factory=dict)   # shared resources spent
    detail: str = ""


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
