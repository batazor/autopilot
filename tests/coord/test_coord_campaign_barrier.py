"""Pure phase-barrier predicate tests."""
from __future__ import annotations

from coord.campaign.barrier import PH_READY, PH_TIMED_OUT, PH_WAITING, phase_outcome
from coord.campaign.model import (
    ALL_REACHED,
    ANY_REACHED,
    DEADLINE_ONLY,
    ELAPSED,
    FLAG_SET,
    PhaseBarrier,
)


def test_all_reached():
    b = PhaseBarrier(ALL_REACHED, signal="q", timeout_s=100)
    assert phase_outcome(b, {"a", "b"}, {"a"}, 0.0, 10.0) == PH_WAITING
    assert phase_outcome(b, {"a", "b"}, {"a", "b"}, 0.0, 10.0) == PH_READY
    # past deadline, not all reached → timed out
    assert phase_outcome(b, {"a", "b"}, {"a"}, 0.0, 100.0) == PH_TIMED_OUT


def test_any_reached():
    b = PhaseBarrier(ANY_REACHED, signal="q", timeout_s=100)
    assert phase_outcome(b, {"a", "b"}, set(), 0.0, 10.0) == PH_WAITING
    assert phase_outcome(b, {"a", "b"}, {"b"}, 0.0, 10.0) == PH_READY


def test_flag_set_single_party():
    b = PhaseBarrier(FLAG_SET, signal="city_empty", timeout_s=600, on_timeout="abort")
    assert phase_outcome(b, {"farm"}, set(), 0.0, 10.0) == PH_WAITING
    assert phase_outcome(b, {"farm"}, {"farm"}, 0.0, 10.0) == PH_READY
    assert phase_outcome(b, {"farm"}, set(), 0.0, 600.0) == PH_TIMED_OUT


def test_elapsed():
    b = PhaseBarrier(ELAPSED, min_dwell_s=30.0, timeout_s=100)
    assert phase_outcome(b, set(), set(), 0.0, 10.0) == PH_WAITING
    assert phase_outcome(b, set(), set(), 0.0, 30.0) == PH_READY


def test_deadline_only():
    b = PhaseBarrier(DEADLINE_ONLY, timeout_s=300)
    assert phase_outcome(b, set(), set(), 0.0, 100.0) == PH_WAITING
    assert phase_outcome(b, set(), set(), 0.0, 300.0) == PH_READY


def test_deadline_only_no_timeout_is_immediate():
    b = PhaseBarrier(DEADLINE_ONLY)
    assert phase_outcome(b, set(), set(), 0.0, 0.0) == PH_READY
