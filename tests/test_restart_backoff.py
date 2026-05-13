from __future__ import annotations

import random
from typing import Any

import pytest

from worker import restart_backoff
from worker.restart_backoff import compute_restart_delay


@pytest.fixture(autouse=True)
def _deterministic_rng(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pin the RNG so jitter is reproducible across runs."""
    rng = random.Random(0xDEADBEEF)
    monkeypatch.setattr(restart_backoff, "_rng", rng)
    yield


def test_attempt_1_uses_base_plus_jitter() -> None:
    """First restart sits within ``base * (1 ± jitter)``."""
    delay = compute_restart_delay(1, base_seconds=10.0, cap_seconds=300.0, jitter=0.25)
    assert 7.5 <= delay <= 12.5


def test_delay_grows_exponentially_until_cap() -> None:
    """Each retry doubles until it hits ``cap_seconds``."""
    delays = [
        compute_restart_delay(n, base_seconds=10.0, cap_seconds=300.0, jitter=0.0)
        for n in range(1, 10)
    ]
    assert delays[0] == 10.0
    assert delays[1] == 20.0
    assert delays[2] == 40.0
    # Eventually saturates at the cap (10 * 2^5 = 320 → 300).
    assert all(d == 300.0 for d in delays[5:])


def test_zero_jitter_is_deterministic() -> None:
    """``jitter=0`` returns the exact capped exponential — useful for tests."""
    assert compute_restart_delay(3, base_seconds=5.0, cap_seconds=1000.0, jitter=0.0) == 20.0


def test_clamps_negative_attempts_to_first_retry() -> None:
    """A bogus ``attempt=0`` shouldn't shorten the delay — treat as attempt 1."""
    a = compute_restart_delay(0, base_seconds=10.0, jitter=0.0)
    b = compute_restart_delay(1, base_seconds=10.0, jitter=0.0)
    assert a == b == 10.0


def test_jitter_stays_non_negative() -> None:
    """Even with a strong negative draw, delay never goes below zero."""
    # Force a negative jitter draw close to the limit.
    class _Rng:
        def uniform(self, a: float, b: float) -> float:
            return a  # most negative

    restart_backoff._rng = _Rng()  # type: ignore[assignment]
    assert compute_restart_delay(1, base_seconds=4.0, jitter=2.0) >= 0.0
