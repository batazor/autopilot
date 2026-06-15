"""Feedback/self-tuning: stall detection, backoff, self-healing, metrics."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    CandidateAction,
    FeedbackState,
    Outcome,
    apply_feedback,
    record,
    record_many,
    tuning,
)


def _stalls(key, domain, n):
    return [Outcome(key, domain, progressed=False, ts=float(i)) for i in range(n)]


def test_records_attempts_and_success_rate():
    s = record_many(FeedbackState(), [
        Outcome("furnace", "building_progression", True, 1),
        Outcome("furnace", "building_progression", False, 2),
    ])
    st = s.stats["furnace"]
    assert st.attempts == 2
    assert st.progressed == 1
    assert st.success_rate == 0.5


def test_consecutive_stalls_trigger_backoff():
    s = record_many(FeedbackState(), _stalls("furnace", "building_progression", 3))
    bias = tuning(s)
    assert "furnace" in bias.stuck
    assert bias.backoff["furnace"] < 1.0


def test_below_threshold_no_backoff():
    s = record_many(FeedbackState(), _stalls("furnace", "building_progression", 2))
    assert tuning(s).stuck == ()


def test_progress_resets_the_streak_self_healing():
    s = record_many(FeedbackState(), _stalls("furnace", "building_progression", 5))
    assert "furnace" in tuning(s).stuck
    s = record(s, Outcome("furnace", "building_progression", True, 9))   # recovered
    assert s.stats["furnace"].consecutive_stalls == 0
    assert tuning(s).stuck == ()                                          # backoff lifted


def test_apply_feedback_penalises_stuck_candidate_priority():
    s = record_many(FeedbackState(), _stalls("furnace", "building_progression", 3))
    bias = tuning(s)
    cands = [
        CandidateAction("building_progression", CONSTRUCTION, "furnace", 850),
        CandidateAction("building_economy", CONSTRUCTION, "sawmill", 520),
    ]
    out = {c.key: c.priority for c in apply_feedback(cands, bias)}
    assert out["furnace"] < 850                       # penalised
    assert out["sawmill"] == 520                      # untouched
    assert out["furnace"] < out["sawmill"]            # stuck top pick now yields


def test_apply_feedback_noop_without_backoff():
    cands = [CandidateAction("building_progression", CONSTRUCTION, "furnace", 850)]
    assert apply_feedback(cands, tuning(FeedbackState()))[0].priority == 850
