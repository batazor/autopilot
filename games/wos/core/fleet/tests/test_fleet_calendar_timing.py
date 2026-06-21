"""Calendar anticipation — idle / prep / active / closing classification."""
from __future__ import annotations

from types import SimpleNamespace

from games.wos.core.fleet.calendar_timing import (
    ACTIVE,
    CLOSING,
    IDLE,
    PREP,
    campaign_timing,
    prep_lead_for,
)


def _win(active, starts_in_s, ends_in_s):
    return SimpleNamespace(active=active, starts_in_s=starts_in_s, ends_in_s=ends_in_s)


def test_no_window_is_idle():
    t = campaign_timing(None, prep_lead_s=1800)
    assert t.phase == IDLE and t.prep_now is False


def test_active_window():
    t = campaign_timing(_win(True, 0, 5000), prep_lead_s=1800)
    assert t.phase == ACTIVE
    assert t.prep_now is False


def test_closing_window():
    t = campaign_timing(_win(True, 0, 300), prep_lead_s=1800)  # ends in 5 min
    assert t.phase == CLOSING


def test_prep_when_inside_lead():
    # opens in 20 min, lead 30 min → prep now
    t = campaign_timing(_win(False, 1200, 5000), prep_lead_s=1800)
    assert t.phase == PREP
    assert t.prep_now is True


def test_idle_when_outside_lead():
    # opens in 2h, lead 30 min → still idle
    t = campaign_timing(_win(False, 7200, 5000), prep_lead_s=1800)
    assert t.phase == IDLE
    assert t.prep_now is False


def test_prep_lead_for():
    assert prep_lead_for("joint_event") == 1800.0
    assert prep_lead_for("reinforcement") == 0.0
    assert prep_lead_for("unknown") == 0.0
