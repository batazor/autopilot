"""Safety / mutual-exclusion + war-hunt suppression."""
from __future__ import annotations

from games.wos.core.fleet.safety import (
    SafetyContext,
    check_dispatch,
    filter_dispatchable,
    would_overlap,
)


def test_raid_allowed_normally():
    assert check_dispatch("farm_raid", ["F", "G"], SafetyContext()).allowed is True


def test_war_suppresses_offensive_raid():
    v = check_dispatch("farm_raid", ["F", "G"], SafetyContext(war_active=True))
    assert v.allowed is False
    assert v.reason == "war_hunt_keep_troops_home"


def test_hunt_suppresses_offensive_raid():
    assert check_dispatch("farm_raid", ["F"], SafetyContext(hunt_active=True)).allowed is False


def test_reinforcement_not_suppressed_in_war():
    # defensive → still allowed while war is active (that's the point)
    assert check_dispatch("reinforcement", ["H"], SafetyContext(war_active=True)).allowed is True


def test_raid_blocked_when_participant_in_event():
    ctx = SafetyContext(event_fids=frozenset({"F"}))
    v = check_dispatch("farm_raid", ["F", "G"], ctx)
    assert v.allowed is False
    assert "participant_in_active_event" in v.reason


def test_event_not_self_excluded():
    # joint_event has no exclusive_with_events flag → not blocked by event_fids
    ctx = SafetyContext(event_fids=frozenset({"A"}))
    assert check_dispatch("joint_event", ["A"], ctx).allowed is True


def test_unknown_campaign_allowed():
    assert check_dispatch("mystery", ["X"], SafetyContext()).allowed is True


def test_would_overlap():
    assert would_overlap(["A", "B"], ["B", "C"]) is True
    assert would_overlap(["A"], ["B", "C"]) is False


def test_filter_dispatchable_splits():
    ctx = SafetyContext(war_active=True)
    runs = [
        ("raid:1", ("farm_raid", ["F", "G"])),       # suppressed in war
        ("reinforce:1", ("reinforcement", ["H"])),   # defensive → allowed
    ]
    allowed, blocked = filter_dispatchable(runs, ctx)
    assert allowed == ["reinforce:1"]
    assert blocked == [("raid:1", "war_hunt_keep_troops_home")]
