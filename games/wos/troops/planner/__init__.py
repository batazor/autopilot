"""Troop-training planner: which troop type + tier to train next.

Composition-greedy by army-share deficit (:func:`rank_troops` / :func:`plan_training`)
plus a value-greedy :func:`plan_next` that picks the deficit-leading camp's highest
unlocked tier and values it by per-unit power — the structured pick the coordinator's
TRAINING channel consumes.
"""

from .planner import (
    DEFAULT_TARGET,
    MAX_TIER,
    NONE,
    PROMOTE,
    SELECTED,
    TRAIN,
    TROOP_TYPES,
    TrainCandidate,
    TrainingPlan,
    plan_next,
    plan_training,
    rank_troops,
)
from .training_costs import (
    TrainTier,
    load_training_costs,
    promote_cost_time,
    tier_cost_time,
    train_eta,
)

__all__ = [
    "DEFAULT_TARGET",
    "MAX_TIER",
    "NONE",
    "PROMOTE",
    "SELECTED",
    "TRAIN",
    "TROOP_TYPES",
    "TrainCandidate",
    "TrainTier",
    "TrainingPlan",
    "load_training_costs",
    "plan_next",
    "plan_training",
    "promote_cost_time",
    "rank_troops",
    "tier_cost_time",
    "train_eta",
]
