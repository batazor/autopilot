"""Shared stamina budget: model, allocator, and demand table (``budget.yaml``).

Pure decision core for distributing the capped, regenerating per-account
stamina pool across competing consumers (intel events, Joe bandit hunts, beast
hunting). The Redis-backed adapter that resolves live state and enqueues the
chosen scenario is wired separately.
"""
from __future__ import annotations

from .allocator import (
    Decision,
    DemandRuntime,
    SupplyRuntime,
    Verdict,
    allocate,
)
from .model import Budget, Demand, Supply

__all__ = [
    "Budget",
    "Decision",
    "Demand",
    "DemandRuntime",
    "Supply",
    "SupplyRuntime",
    "Verdict",
    "allocate",
]
