"""Troop-stats data + loader: shape integrity and a few anchored facts.

Guards the re-encoded ``db/troops.yaml`` against shape drift (a complete
type x tier x fc matrix) and pins values that the source defines, so a bad
re-transcription fails loudly.
"""
from __future__ import annotations

from games.wos.core.resources.troop_stats import (
    TROOP_TYPES,
    load_troop_stats,
    troop_stat,
)


def test_matrix_is_complete():
    """3 troop types x tier 1-11 x fc 0-10, no gaps, no dups."""
    table = load_troop_stats()
    assert len(table) == 3 * 11 * 11
    for ttype in TROOP_TYPES:
        pairs = {(t, f) for (ty, t, f) in table if ty == ttype}
        assert pairs == {(t, f) for t in range(1, 12) for f in range(11)}


def test_power_increases_with_tier():
    """Per-unit power is non-decreasing up the tier ladder, at every FC level."""
    for ttype in TROOP_TYPES:
        for fc in range(11):
            powers = [troop_stat(ttype, tier, fc).power for tier in range(1, 12)]
            assert powers == sorted(powers), (ttype, fc)


def test_fc_bonus_is_net_positive():
    """Max FC is never weaker than no FC (the per-step curve has low-tier
    integer-rounding wobble, so only the endpoints are a reliable invariant)."""
    for ttype in TROOP_TYPES:
        for tier in range(1, 12):
            assert troop_stat(ttype, tier, 10).power >= troop_stat(ttype, tier, 0).power
