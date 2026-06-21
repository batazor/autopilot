"""Participant selection by role / opt-in / alliance."""
from __future__ import annotations

from games.wos.core.fleet.participants import (
    Candidate,
    select_farm_raid,
    select_joint_event,
    select_participants,
    select_reinforcement,
)


def _c(fid, inst, **kw):
    base = {"online": True, "alliance": "WOLF", "role": "balanced"}
    base.update(kw)
    return Candidate(fid=fid, instance_id=inst, **base)


def test_joint_event_picks_largest_alliance_only():
    cands = [
        _c("1", "dev-a", events_opt_in=True, alliance="WOLF"),
        _c("2", "dev-a", events_opt_in=True, alliance="WOLF"),
        _c("3", "dev-b", events_opt_in=True, alliance="BEAR"),
        _c("4", "dev-c", events_opt_in=False, alliance="WOLF"),  # not opted in
    ]
    parts = select_joint_event(cands)
    assert {p.fid for p in parts} == {"1", "2"}  # WOLF opted-in only; BEAR is smaller
    # 1 and 2 share dev-a → flagged
    assert all(p.shares_device for p in parts)


def test_joint_event_respects_max_n():
    cands = [_c(str(i), f"dev-{i}", events_opt_in=True) for i in range(5)]
    assert len(select_joint_event(cands, max_n=3)) == 3


def test_joint_event_empty_when_none_opted_in():
    cands = [_c("1", "dev-a", events_opt_in=False)]
    assert select_joint_event(cands) == []


def test_farm_raid_pairs_farm_and_fighter_same_alliance():
    cands = [
        _c("F", "dev-a", raid_role="farm"),
        _c("G", "dev-b", raid_role="fighter"),
        _c("X", "dev-c", raid_role="fighter", alliance="BEAR"),
    ]
    parts = select_farm_raid(cands)
    assert [(p.fid, p.role) for p in parts] == [("F", "farm"), ("G", "fighter")]


def test_farm_raid_no_pair_when_only_one_role():
    cands = [_c("F", "dev-a", raid_role="farm")]
    assert select_farm_raid(cands) == []


def test_farm_raid_skips_offline():
    cands = [
        _c("F", "dev-a", raid_role="farm", online=False),
        _c("G", "dev-b", raid_role="fighter"),
    ]
    assert select_farm_raid(cands) == []


def test_reinforcement_picks_allied_helpers_excluding_victim():
    cands = [
        _c("V", "dev-a", reinforce_opt_in=True),                 # the victim
        _c("H1", "dev-b", reinforce_opt_in=True),
        _c("H2", "dev-c", reinforce_opt_in=True, alliance="BEAR"),  # other alliance
        _c("H3", "dev-d", reinforce_opt_in=False),               # not opted in
    ]
    parts = select_reinforcement(cands, victim_fid="V")
    assert {p.fid for p in parts} == {"H1"}
    assert all(p.role == "helper" for p in parts)


def test_dispatch_by_campaign_id():
    cands = [
        _c("F", "dev-a", raid_role="farm"),
        _c("G", "dev-b", raid_role="fighter"),
    ]
    assert len(select_participants("farm_raid", cands)) == 2
    assert select_participants("unknown", cands) == []
