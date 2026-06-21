"""Shared resource world: model, allocator, and action table (``actions.yaml``).

Pure decision core for spending a player's limited, multi-kind resources —
march slots (concurrency), stamina (regen pool), troops (typed pool) and heroes
(exclusive set) — across competing raids/marches. One in-game action spends
several resources at once, so each action declares a cost *vector* and the
allocator checks the whole bundle. The Redis-backed adapter resolves live state,
holds reservations, and enqueues the chosen scenario; it is wired into the
scheduler behind ``actions.yaml``'s ``enabled`` flag.
"""
from __future__ import annotations

from .allocator import (
    CONSUME,
    IDLE,
    ActionRuntime,
    Decision,
    Verdict,
    allocate,
)
from .model import (
    Action,
    ActionTable,
    Affordability,
    Assignment,
    Block,
    Cost,
    ResourceSpec,
    WorldView,
    can_afford,
)

__all__ = [
    "CONSUME",
    "IDLE",
    "Action",
    "ActionRuntime",
    "ActionTable",
    "Affordability",
    "Assignment",
    "Block",
    "Cost",
    "Decision",
    "ResourceSpec",
    "Verdict",
    "WorldView",
    "allocate",
    "can_afford",
]
