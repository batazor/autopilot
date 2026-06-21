"""Troop-training planner: which troop type to train next (army-composition greedy)."""

from .planner import DEFAULT_TARGET, TROOP_TYPES, plan_training, rank_troops

__all__ = ["DEFAULT_TARGET", "TROOP_TYPES", "plan_training", "rank_troops"]
