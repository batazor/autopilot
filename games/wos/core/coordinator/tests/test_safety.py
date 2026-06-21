"""Situational safety: suppress exposing actions, inject defensive ones."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    MARCH,
    CandidateAction,
    Channel,
    ThreatState,
    apply_safety,
    assess_safety,
    coordinate,
)
from games.wos.core.coordinator.safety import (
    HEAL_INJURED,
    RECALL_MARCHES,
    SHIELD_UP,
)

HOUR = 3600


def _kinds(directive):
    return [a.kind for a in directive.actions]


def test_no_threat_is_passive():
    d = assess_safety(ThreatState())
    assert d.safe_mode is False
    assert d.suppress_domains == ()
    assert d.actions == ()


def test_incoming_attack_no_shield_raises_shield_and_suppresses():
    d = assess_safety(ThreatState(incoming_attack=True, attack_eta_s=300))
    assert d.safe_mode is True
    assert SHIELD_UP in _kinds(d)
    assert set(d.suppress_domains) == {"gather", "raids"}     # don't send troops out


def test_incoming_attack_with_exposed_troops_recalls():
    d = assess_safety(ThreatState(incoming_attack=True, troops_exposed=True, shield_active=True,
                                  shield_remaining_s=10 * HOUR))
    assert RECALL_MARCHES in _kinds(d)
    assert SHIELD_UP not in _kinds(d)                          # already shielded, lots left


def test_pvp_window_shielded_just_suppresses():
    d = assess_safety(ThreatState(pvp_window=True, shield_active=True, shield_remaining_s=10 * HOUR))
    assert d.safe_mode is True
    assert set(d.suppress_domains) == {"gather", "raids"}
    assert d.actions == ()                                     # shielded & not expiring


def test_shield_refreshed_when_expiring_in_danger():
    d = assess_safety(ThreatState(pvp_window=True, shield_active=True, shield_remaining_s=10 * 60))
    assert SHIELD_UP in _kinds(d)                              # < 1h left → refresh


def test_gatherers_under_attack_recalls_them_and_holds_new_gathers():
    d = assess_safety(ThreatState(gatherers_under_attack=True))
    assert d.safe_mode is True
    recalls = [a for a in d.actions if a.kind == RECALL_MARCHES]
    assert recalls and recalls[0].target == "gather"     # recall the gatherers specifically
    assert "gather" in d.suppress_domains                 # don't feed new gathers in
    assert SHIELD_UP not in _kinds(d)                     # a shield can't save map troops


def test_gather_node_attack_does_not_suppress_raids():
    # Only the gather node is contested (not the city) → raids aren't blocked.
    d = assess_safety(ThreatState(gatherers_under_attack=True))
    assert "raids" not in d.suppress_domains


def test_injured_triggers_heal_without_danger():
    d = assess_safety(ThreatState(injured=1200))
    assert d.safe_mode is False                                # heal is recovery, not danger
    assert _kinds(d) == [HEAL_INJURED]


def test_actions_are_urgency_ordered():
    d = assess_safety(ThreatState(incoming_attack=True, troops_exposed=True, injured=500))
    # shield (100) before recall (90) before heal (30)
    assert _kinds(d) == [SHIELD_UP, RECALL_MARCHES, HEAL_INJURED]


def test_apply_safety_drops_exposing_candidates():
    cands = [
        CandidateAction("gather", MARCH, "gather_coal", 720),
        CandidateAction("building_progression", CONSTRUCTION, "furnace", 850),
    ]
    d = assess_safety(ThreatState(pvp_window=True))
    kept = apply_safety(cands, d)
    assert [c.key for c in kept] == ["furnace"]               # gather suppressed


def test_safe_mode_end_to_end_blocks_gather_keeps_build():
    cands = [
        CandidateAction("gather", MARCH, "gather_coal", 720),
        CandidateAction("building_progression", CONSTRUCTION, "furnace", 850),
    ]
    d = assess_safety(ThreatState(incoming_attack=True))
    dec = coordinate([Channel("m1", MARCH), Channel("c1", CONSTRUCTION)], apply_safety(cands, d), {})
    keys = {c.action.key for c in dec.commits}
    assert keys == {"furnace"}                                # gather never reaches a march lane
