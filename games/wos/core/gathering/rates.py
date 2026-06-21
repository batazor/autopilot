"""Gathering yield/time math — the WoS gather-rate formula as game facts.

Full-node gather time is a fixed base divided by the total gathering-speed boost;
the amount collected scales linearly with how long the march stays out (capped at
the node's max). Re-encoded from public game data (deepfriedmind/wos-toolkit,
AGPL — formula + numbers only, no code). Pure.

Why this matters: the economy layer ([[economy_bias]]) picks *which* resource is
short but had no model of *how much* a gather returns. ``yield_per_hour`` is the
ROI numerator for spending a march on gather vs. anything else.
"""
from __future__ import annotations

# Seconds to fully gather a node at +0% gathering-speed boost (the in-game base,
# ~32h 44m — a fixed magic constant the game uses for a full node).
BASE_GATHER_TIME_S = 117_847

# Hero expedition-skill gathering-speed bonus, by skill level (percent).
EXPEDITION_SKILL_BONUS_PCT: dict[int, int] = {1: 5, 2: 10, 3: 15, 4: 20, 5: 25}
# "Gathering Speed" city bonus (percent), when active.
CITY_BONUS_PCT = 100

# Max yield of a level-8 resource node (reference; scales with node level).
NODE_MAX_LV8: dict[str, int] = {
    "meat": 14_000_000,
    "wood": 14_000_000,
    "coal": 2_800_000,
    "iron": 700_000,
}


def total_boost_pct(
    *,
    node_pct: float = 0.0,
    expedition_level: int = 0,
    city_bonus: bool = False,
    extra_pct: float = 0.0,
) -> float:
    """Combined gathering-speed boost (additive percents)."""
    boost = float(node_pct) + float(extra_pct)
    boost += EXPEDITION_SKILL_BONUS_PCT.get(int(expedition_level), 0)
    if city_bonus:
        boost += CITY_BONUS_PCT
    return boost


def gather_time_s(boost_pct: float = 0.0) -> float:
    """Seconds to fully gather a node at ``boost_pct`` gathering-speed boost."""
    return BASE_GATHER_TIME_S / (1.0 + max(0.0, boost_pct) / 100.0)


def gathered_amount(node_max: int, available_s: float, boost_pct: float = 0.0) -> int:
    """Amount collected if the march gathers for ``available_s`` (capped at ``node_max``)."""
    full = gather_time_s(boost_pct)
    if available_s >= full:
        return int(node_max)
    return int(node_max * (max(0.0, available_s) / full))


def yield_per_hour(node_max: int, boost_pct: float = 0.0) -> float:
    """Full-node yield ÷ full-node time, in units/hour — the gather ROI numerator."""
    return node_max / (gather_time_s(boost_pct) / 3600.0)
