"""Arena reward-window timing: beta tier boundaries, UTC+8 conversion, policy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from games.wos.core.arena.reward_window import (
    BETA_WINDOWS,
    STANDARD_WINDOWS,
    FightDecision,
    Tier,
    classify,
    high_return_window,
    minute_of_day,
    server_local,
    should_fight,
)


def _utc8(hour: int, minute: int = 0) -> datetime:
    """A real UTC instant that reads as ``hour:minute`` on the UTC+8 wall clock."""
    # UTC+8 == UTC + 8h, so subtract 8h from the desired local time. Anchored on
    # a fixed date (no Date.now) — the math is date-independent.
    local = datetime(2026, 6, 19, hour, minute, tzinfo=UTC)
    return local - timedelta(hours=8)


# --- window sets are well-formed --------------------------------------------

@pytest.mark.parametrize("windows", [BETA_WINDOWS, STANDARD_WINDOWS])
def test_windows_tile_the_day_without_gaps(windows):
    assert windows[0][0] == 0
    assert windows[-1][1] == 24 * 60
    for (_, end_prev, _), (start_next, _, _) in pairwise(windows):
        assert end_prev == start_next  # contiguous, no gap or overlap


# --- UTC -> UTC+8 conversion -------------------------------------------------

def test_server_local_shifts_by_eight_hours():
    # 14:00 UTC -> 22:00 UTC+8.
    local = server_local(datetime(2026, 6, 19, 14, 0, tzinfo=UTC))
    assert (local.hour, local.minute) == (22, 0)


def test_naive_datetime_assumed_utc():
    assert minute_of_day(datetime(2026, 6, 19, 14, 0)) == 22 * 60  # noqa: DTZ001 — naive-input case


def test_conversion_wraps_past_midnight():
    # 16:00 UTC -> 00:00 next day UTC+8 -> minute 0.
    assert minute_of_day(datetime(2026, 6, 19, 16, 0, tzinfo=UTC)) == 0


# --- beta tier boundaries (half-open) ---------------------------------------

@pytest.mark.parametrize(
    ("hour", "minute", "tier"),
    [
        (0, 0, Tier.HIGHER),      # midnight — start of higher window
        (12, 0, Tier.HIGHER),
        (21, 59, Tier.HIGHER),    # last minute before the cut
        (22, 0, Tier.NORMAL),     # boundary belongs to the next window
        (22, 30, Tier.NORMAL),
        (23, 29, Tier.NORMAL),
        (23, 30, Tier.REDUCED),   # beta cut to reduced
        (23, 45, Tier.REDUCED),
        (23, 59, Tier.REDUCED),
    ],
)
def test_beta_tier_at_boundaries(hour, minute, tier):
    assert classify(_utc8(hour, minute)).tier is tier


def test_status_countdowns():
    status = classify(_utc8(23, 30))  # exactly at the reduced cut
    assert status.tier is Tier.REDUCED
    assert status.minutes_into_day == 23 * 60 + 30
    assert status.minutes_until_reset == 30
    assert status.minutes_until_next_tier == 30  # next boundary is the reset
    assert status.is_reduced and not status.is_high_return


def test_high_return_flags_and_until_next_tier():
    status = classify(_utc8(21, 0))
    assert status.is_high_return
    assert status.minutes_until_next_tier == 60   # 21:00 -> 22:00
    assert status.minutes_until_reset == 3 * 60    # 21:00 -> 24:00


# --- beta vs. standard: the one rule that differs ---------------------------

def test_beta_holds_normal_where_standard_is_reduced():
    at_23_15 = _utc8(23, 15)
    assert classify(at_23_15, BETA_WINDOWS).tier is Tier.NORMAL
    assert classify(at_23_15, STANDARD_WINDOWS).tier is Tier.REDUCED


# --- fight policy ------------------------------------------------------------

def test_no_challenges_never_fights():
    d = should_fight(_utc8(10, 0), challenges_remaining=0)
    assert d == FightDecision(False, Tier.HIGHER, "no_challenges", minutes_until_reset=14 * 60)


@pytest.mark.parametrize(
    ("hour", "minute", "tier"),
    [
        (10, 0, Tier.HIGHER),
        (21, 59, Tier.HIGHER),
        (22, 0, Tier.NORMAL),
        (23, 29, Tier.NORMAL),
    ],
)
def test_fights_in_high_and_normal_windows(hour, minute, tier):
    d = should_fight(_utc8(hour, minute), challenges_remaining=3)
    assert d.fight is True
    assert d.tier is tier
    assert d.reason == f"fight_{tier.value}_window"


def test_reduced_window_fights_by_default_use_or_lose():
    d = should_fight(_utc8(23, 45), challenges_remaining=2)
    assert d.fight is True
    assert d.tier is Tier.REDUCED
    assert d.reason == "fight_reduced_window"


def test_reduced_window_can_be_skipped():
    d = should_fight(_utc8(23, 45), challenges_remaining=2, skip_reduced=True)
    assert d.fight is False
    assert d.reason == "skip_reduced_window"


def test_skip_reduced_does_not_affect_normal_window():
    d = should_fight(_utc8(23, 0), challenges_remaining=2, skip_reduced=True)
    assert d.fight is True  # 23:00 is still NORMAL under beta


# --- scheduler hint ----------------------------------------------------------

def test_high_return_window_bounds():
    assert high_return_window(BETA_WINDOWS) == (0, 22 * 60)
    assert high_return_window(STANDARD_WINDOWS) == (0, 22 * 60)
