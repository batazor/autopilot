"""The verdict vocabulary every planner and allocator reports.

Fifteen modules declared these strings independently — ``SELECTED`` in thirteen
of them, ``NONE`` in nine, ``INSUFFICIENT_RESOURCES`` in seven. Same spelling,
same meaning, no single place to read the list, and nothing stopping the
sixteenth planner from inventing ``no_resources`` instead.

These are what `botctl why` and the /planner UI show an operator when they ask
why a domain picked nothing, so the vocabulary being consistent across domains
is the whole point of having it.

**Values are frozen.** They are compared as plain strings, ride in API payloads
and decision traces, and are asserted verbatim by tests — changing one is a
behaviour change, not a rename. Same discipline as :mod:`tasks.reasons`.

Two overlapping vocabularies live here because ``SELECTED`` belongs to both:

* a *planner* answers "what should this domain do next" and explains a refusal;
* an *allocator* (stamina, resources) answers "which competing demand wins" and
  explains each loser.
"""

from __future__ import annotations

# --- shared -----------------------------------------------------------------
SELECTED = "selected"
"""Something was chosen; the plan carries it."""

# --- planner refusals -------------------------------------------------------
NONE = "none"
"""Nothing left to do — every track is already at its target."""

LOCKED = "locked"
"""The feature is not unlocked yet (a Furnace-level gate, typically)."""

INSUFFICIENT_RESOURCES = "insufficient_resources"
"""A step exists but its cost is not affordable right now."""

INSUFFICIENT_STAMINA = "insufficient_stamina"
"""Stamina-priced domains: the estimate is below this demand's cost."""

ALL_MAXED = "all_maxed"
"""Every candidate is at max level — distinct from NONE, which can be temporary."""

# --- allocator per-demand verdicts ------------------------------------------
WINDOW_CLOSED = "window_closed"
"""``active_when`` is false at this moment."""

QUOTA_FULL = "quota_full"
"""The daily quota for this demand is exhausted."""

RESERVE_HELD = "reserve_held"
"""Held back on purpose for a higher-priority demand."""

NOT_CONSIDERED = "not_considered"
"""A higher-priority demand already took the supply."""

# --- allocator decision actions ---------------------------------------------
CONSUME = "consume"
SUPPLY = "supply"
IDLE = "idle"
"""Nothing to spend on right now — an outcome, not a failure."""


PLANNER_REASONS: frozenset[str] = frozenset(
    {
        SELECTED,
        NONE,
        LOCKED,
        INSUFFICIENT_RESOURCES,
        INSUFFICIENT_STAMINA,
        ALL_MAXED,
        WINDOW_CLOSED,
        QUOTA_FULL,
        RESERVE_HELD,
        NOT_CONSIDERED,
        CONSUME,
        SUPPLY,
        IDLE,
    }
)
