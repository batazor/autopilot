"""_jittered_wait_seconds contract: deterministic when pct<=0, bounded when pct>0."""
from __future__ import annotations

import random

from tasks.dsl_scenario_helpers import _jittered_wait_seconds


def test_zero_pct_is_passthrough() -> None:
    assert _jittered_wait_seconds(2.0, 0.0) == 2.0


def test_negative_pct_is_passthrough() -> None:
    assert _jittered_wait_seconds(2.0, -0.5) == 2.0


def test_zero_seconds_is_passthrough() -> None:
    assert _jittered_wait_seconds(0.0, 0.5) == 0.0


def test_jitter_stays_within_band() -> None:
    rng = random.Random(0)
    base = 2.0
    pct = 0.15
    lo, hi = base * (1 - pct), base * (1 + pct)
    for _ in range(200):
        # Re-seed shared module RNG so the helper draws from a known stream.
        random.seed(rng.random())
        out = _jittered_wait_seconds(base, pct)
        assert lo <= out <= hi


def test_pct_above_one_is_clamped_to_one() -> None:
    # ``pct >= 1.0`` clamps to 1.0; jittered output stays ≥ 0.
    for _ in range(200):
        out = _jittered_wait_seconds(2.0, 5.0)
        assert 0.0 <= out <= 4.0
