"""Pure barrier state-machine tests (deterministic, now injected)."""
from __future__ import annotations

from coord.barrier_logic import evaluate
from coord.models import (
    BARRIER_ABORTED,
    BARRIER_READY,
    BARRIER_TIMED_OUT,
    BARRIER_WAITING,
    BarrierSpec,
)


def _spec(n=2, deadline=100.0):
    return BarrierSpec(barrier_id="b1", required_n=n, deadline_ts=deadline)


def test_waiting_below_quorum_before_deadline():
    assert evaluate(_spec(), {"a"}, now=50.0) == BARRIER_WAITING


def test_ready_at_quorum():
    assert evaluate(_spec(), {"a", "b"}, now=50.0) == BARRIER_READY


def test_ready_overrides_deadline():
    # quorum met even though now is past the deadline → READY, not TIMED_OUT.
    assert evaluate(_spec(), {"a", "b"}, now=999.0) == BARRIER_READY


def test_timed_out_below_quorum_past_deadline():
    assert evaluate(_spec(), {"a"}, now=100.0) == BARRIER_TIMED_OUT


def test_abort_takes_precedence():
    assert evaluate(_spec(), {"a", "b"}, now=50.0, aborted=True) == BARRIER_ABORTED


def test_duplicate_arrivals_dont_double_count():
    # a set is enforced internally; the same party arriving twice is still 1.
    assert evaluate(_spec(n=2), ["a", "a"], now=50.0) == BARRIER_WAITING


def test_single_party_flag_barrier():
    # FLAG_SET-style: required_n=1, one arrival flips READY (the farm "city_empty").
    assert evaluate(_spec(n=1), {"farm"}, now=10.0) == BARRIER_READY
