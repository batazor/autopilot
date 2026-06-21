"""The WoS campaign catalog builds + validates, all disabled by default."""
from __future__ import annotations

from games.wos.core.fleet import step_kinds as sk
from games.wos.core.fleet.catalog import build_campaign_defs

from coord.campaign import TRIGGER_CALENDAR, TRIGGER_MANUAL, TRIGGER_NOTIFY


def test_three_campaigns_present():
    defs = build_campaign_defs()
    assert set(defs) == {"joint_event", "farm_raid", "reinforcement"}


def test_all_ship_disabled():
    for cdef in build_campaign_defs().values():
        assert cdef.enabled is False


def test_joint_event_is_calendar_anchored():
    j = build_campaign_defs()["joint_event"]
    assert j.trigger == TRIGGER_CALENDAR
    assert j.anchor_event_slug == "power_up"
    assert [p.name for p in j.phases] == ["gather_points", "converge", "claim"]


def test_farm_raid_safety_gate_and_rollback():
    r = build_campaign_defs()["farm_raid"]
    assert r.trigger == TRIGGER_MANUAL
    recall = r.phases[0]
    assert recall.barrier.signal == "city_empty"
    assert recall.barrier.on_timeout == "abort"
    # both pre-resume phases roll back to resuming farm troops
    assert r.phases[0].rollback[0].scenario == "city.resume_troops"
    assert r.phases[1].rollback[0].scenario == "city.resume_troops"


def test_reinforcement_is_notify_triggered_and_tight():
    r = build_campaign_defs()["reinforcement"]
    assert r.trigger == TRIGGER_NOTIFY
    assert r.default_ttl_s == 600.0


def test_steps_reference_known_kinds():
    known = sk.WIRED | sk.DEFERRED
    for cdef in build_campaign_defs().values():
        for phase in cdef.phases:
            for step in (*phase.steps, *phase.rollback):
                assert step.kind in known
