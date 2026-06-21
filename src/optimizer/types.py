"""Data classes shared across the optimizer modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Cost:
    """One resource cost on a candidate. Multiple costs combine on the

    same candidate (e.g. ``hero_xp`` + ``gems`` if we later add reset)."""

    resource: str
    amount: int


@dataclass(frozen=True)
class Candidate:
    """A single next-step upgrade action proposed for evaluation.

    Fields mirror the deep-research-report's semantic command shape, but
    pruned to what the MVP scorer + greedy executor actually need.
    """

    id: str
    """Unique identifier, e.g. ``level_up:molly:7→8``."""
    action: str
    """Action type. Currently ``level_up``; later ``skill_up`` /
    ``star_tier_up`` / ``gear_assign`` / ``resource_rules``."""
    hero_id: str | None
    """Target hero for hero-bound actions. ``None`` for global rules."""
    priority_band: str = "core"
    """``core`` / ``threshold`` / ``long_term_core`` / ``context``. Used
    for tie-breaks and explainability."""
    costs: tuple[Cost, ...] = ()
    """Resource cost(s) the executor must pay."""
    preconditions: tuple[str, ...] = ()
    """Human-readable preconditions for the debug page."""
    payload: dict[str, Any] = field(default_factory=dict)
    """Action-specific details (e.g. ``from_level``/``to_level`` for level_up).
    Free-form so renderers / executors can pull whatever they need."""

    def label(self) -> str:
        if self.hero_id:
            return f"{self.action} · {self.hero_id}"
        return self.action
