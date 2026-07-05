"""Feedback/self-tuning: stall detection, backoff, self-healing, metrics."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    CandidateAction,
    FeedbackState,
    Outcome,
    Utility,
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
        CandidateAction("building_progression", CONSTRUCTION, "furnace", Utility(base_value=850)),
        CandidateAction("building_economy", CONSTRUCTION, "sawmill", Utility(base_value=520)),
    ]
    out = {c.key: c.priority for c in apply_feedback(cands, bias)}
    assert out["furnace"] < 850                       # penalised
    assert out["sawmill"] == 520                      # untouched
    assert out["furnace"] < out["sawmill"]            # stuck top pick now yields


def test_apply_feedback_noop_without_backoff():
    cands = [CandidateAction("building_progression", CONSTRUCTION, "furnace", Utility(base_value=850))]
    assert apply_feedback(cands, tuning(FeedbackState()))[0].priority == 850


# --- reason tracking + circuit-breaker -------------------------------------


def _same_reason_stalls(key, domain, n, *, reason, start_ts=0.0):
    return [
        Outcome(key, domain, progressed=False, ts=start_ts + i, reason=reason)
        for i in range(n)
    ]


def test_same_reason_streak_accumulates_and_resets_on_reason_change():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 2, reason="nav_error")
    )
    assert s.stats["intel:run"].same_reason_streak == 2
    s = record(s, Outcome("intel:run", "intel", False, 5, reason="timeout"))
    assert s.stats["intel:run"].same_reason_streak == 1   # new diagnosis → new streak
    assert s.stats["intel:run"].consecutive_stalls == 3   # total stall streak keeps counting


def test_anonymous_failures_never_arm_the_breaker():
    s = record_many(FeedbackState(), _stalls("intel:run", "intel", 5))  # reason=""
    assert s.stats["intel:run"].same_reason_streak == 0
    bias = tuning(s, now=100.0)
    assert bias.held == ()                        # soft backoff only
    assert "intel:run" in bias.stuck


def test_breaker_holds_after_same_reason_threshold():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 3, reason="nav_error")
    )
    bias = tuning(s, now=10.0)
    assert bias.held == ("intel:run",)
    assert "intel:run" not in bias.backoff        # held, not merely deprioritised


def test_breaker_half_opens_after_cooldown():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 3, reason="nav_error")
    )
    last_ts = s.stats["intel:run"].last_ts
    bias = tuning(s, now=last_ts + 3601.0)
    assert bias.held == ()                        # window elapsed → allowed to retry
    assert "intel:run" in bias.stuck              # still soft-backed-off until a success


def test_breaker_never_trips_without_now():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 3, reason="nav_error")
    )
    assert tuning(s).held == ()                   # pure/offline callers: backoff only


def test_progress_resets_reason_streak():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 3, reason="nav_error")
    )
    s = record(s, Outcome("intel:run", "intel", True, 9))
    st = s.stats["intel:run"]
    assert st.same_reason_streak == 0
    assert st.last_reason == ""
    assert tuning(s, now=10.0).held == ()


def test_apply_feedback_drops_held_candidates():
    s = record_many(
        FeedbackState(), _same_reason_stalls("intel:run", "intel", 3, reason="nav_error")
    )
    bias = tuning(s, now=10.0)
    cands = [
        CandidateAction("intel", CONSTRUCTION, "intel:run", Utility(base_value=760)),
        CandidateAction("gather", CONSTRUCTION, "gather:meat", Utility(base_value=450)),
    ]
    out = apply_feedback(cands, bias)
    assert [c.key for c in out] == ["gather:meat"]
