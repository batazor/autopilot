"""Coordinator core: cross-channel allocation under a shared resource budget."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    MARCH,
    RESEARCH,
    CandidateAction,
    Channel,
    coordinate,
)


def _cand(domain, kind, key, priority, cost=None):
    return CandidateAction(domain=domain, channel_kind=kind, key=key,
                           priority=priority, cost=cost or {})


def test_fills_channels_by_kind_and_priority():
    channels = [Channel("c1", CONSTRUCTION), Channel("r1", RESEARCH)]
    cands = [
        _cand("research", RESEARCH, "tech", 900),
        _cand("building_progression", CONSTRUCTION, "furnace", 850),
    ]
    dec = coordinate(channels, cands, {})
    placed = {c.action.key: c.channel_id for c in dec.commits}
    assert placed == {"tech": "r1", "furnace": "c1"}
    assert dec.no_channel == ()


def test_shared_budget_couples_domains():
    # Two construction lanes, but only enough wood for one — priority wins.
    channels = [Channel("c1", CONSTRUCTION), Channel("c2", CONSTRUCTION)]
    cands = [
        _cand("building_progression", CONSTRUCTION, "A", 850, {"wood": 100}),
        _cand("building_economy", CONSTRUCTION, "B", 520, {"wood": 100}),
    ]
    dec = coordinate(channels, cands, {"wood": 150})
    assert [c.action.key for c in dec.commits] == ["A"]
    assert [c.key for c in dec.starved] == ["B"]
    assert dec.bottleneck_resources == ("wood",)
    assert dec.remaining["wood"] == 50


def test_no_channel_when_kind_absent():
    dec = coordinate([Channel("c1", CONSTRUCTION)], [_cand("raids", MARCH, "beast", 600)], {})
    assert dec.commits == ()
    assert [c.key for c in dec.no_channel] == ["beast"]


def test_starved_high_priority_does_not_waste_the_channel():
    # One lane: the expensive top pick can't pay, so the cheap one takes it.
    channels = [Channel("c1", CONSTRUCTION)]
    cands = [
        _cand("building_progression", CONSTRUCTION, "expensive", 850, {"wood": 1000}),
        _cand("building_economy", CONSTRUCTION, "cheap", 520, {"wood": 10}),
    ]
    dec = coordinate(channels, cands, {"wood": 100})
    assert [c.action.key for c in dec.commits] == ["cheap"]
    assert [c.key for c in dec.starved] == ["expensive"]
    assert dec.remaining["wood"] == 90


def test_two_same_kind_channels_both_filled():
    channels = [Channel("c1", CONSTRUCTION), Channel("c2", CONSTRUCTION)]
    cands = [
        _cand("building_progression", CONSTRUCTION, "A", 850, {"wood": 30}),
        _cand("building_economy", CONSTRUCTION, "B", 520, {"wood": 20}),
    ]
    dec = coordinate(channels, cands, {"wood": 100})
    assert {c.action.key for c in dec.commits} == {"A", "B"}
    assert dec.remaining["wood"] == 50
    assert dec.bottleneck_resources == ()


def test_committed_for_filters_by_kind():
    channels = [Channel("c1", CONSTRUCTION), Channel("r1", RESEARCH)]
    cands = [_cand("research", RESEARCH, "t", 900), _cand("building_progression", CONSTRUCTION, "f", 850)]
    dec = coordinate(channels, cands, {})
    assert [c.action.key for c in dec.committed_for(RESEARCH)] == ["t"]
    assert [c.action.key for c in dec.committed_for(CONSTRUCTION)] == ["f"]
