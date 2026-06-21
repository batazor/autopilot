"""ROI gate for renting the locked 2nd construction queue with diamonds."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BuildCandidate,
    BuildSlate,
    evaluate_queue_rental,
)
from games.wos.core.building.planner.queue_rental import (
    ALREADY_OPEN,
    INSUFFICIENT_DIAMONDS,
    LOW_VALUE,
    NO_WORK,
    WORTH_IT,
)

HOUR = 3600


def _c(instance_id, time_s, *, affordable=True):
    return BuildCandidate(
        instance_id=instance_id, spec_id=instance_id, track="economy",
        to_level="1", to_rank=1.0, value=50.0, cost_total=100,
        affordable=affordable, time_s=time_s,
    )


def _slate(picks, candidates):
    return BuildSlate(picks=tuple(picks), candidates=tuple(candidates), reason="selected")


def test_already_open_when_not_locked():
    s = _slate([_c("a", HOUR)], [_c("a", HOUR), _c("b", HOUR)])
    d = evaluate_queue_rental(s, second_queue_locked=False, diamonds=10_000)
    assert d.rent is False
    assert d.reason == ALREADY_OPEN


def test_no_work_when_only_one_candidate():
    s = _slate([_c("a", HOUR)], [_c("a", HOUR)])
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=10_000)
    assert d.rent is False
    assert d.reason == NO_WORK


def test_worth_it_many_quick_builds():
    # 30 one-hour builds behind queue 1 → fills the 24h window.
    cands = [_c(f"q{i}", HOUR) for i in range(30)]
    s = _slate([cands[0]], cands)
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=5_000)
    assert d.rent is True
    assert d.reason == WORTH_IT
    assert d.utilisation >= 0.6


def test_worth_it_single_long_build():
    # One 13h build alone (util 0.54 < 0.6) still qualifies via the long-build path.
    s = _slate([_c("a", HOUR)], [_c("a", HOUR), _c("long", 13 * HOUR)])
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=5_000)
    assert d.rent is True
    assert d.reason == WORTH_IT
    assert d.candidate.instance_id == "long"


def test_low_value_lone_medium_build():
    # A single 6h build behind queue 1 → mostly idle, not long → skip.
    s = _slate([_c("a", HOUR)], [_c("a", HOUR), _c("b", 6 * HOUR)])
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=10_000)
    assert d.rent is False
    assert d.reason == LOW_VALUE


def test_insufficient_diamonds_even_when_worth_it():
    s = _slate([_c("a", HOUR)], [_c("a", HOUR), _c("long", 13 * HOUR)])
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=1_000)
    assert d.rent is False
    assert d.reason == INSUFFICIENT_DIAMONDS


def test_unaffordable_candidates_excluded_from_pool():
    # The only 2nd-queue candidate is unaffordable → no work for the rented queue.
    s = _slate([_c("a", HOUR)], [_c("a", HOUR), _c("b", 13 * HOUR, affordable=False)])
    d = evaluate_queue_rental(s, second_queue_locked=True, diamonds=10_000)
    assert d.rent is False
    assert d.reason == NO_WORK
