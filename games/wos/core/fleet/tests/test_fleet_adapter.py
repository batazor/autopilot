"""Adapter unit tests that need no Redis: config overlay, run (de)serialization,
and the planner-input view adapters."""
from __future__ import annotations

from types import SimpleNamespace

from games.wos.core.fleet import adapter

from coord.campaign import CampaignRun, Participant, ParticipantStatus
from coord.campaign.model import RUNNING


def _write_cfg(tmp_path, text):
    p = tmp_path / "fleet.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_campaigns_disabled_by_default(tmp_path):
    p = _write_cfg(tmp_path, "enabled: false\ncampaigns: {joint_event: true}\n")
    defs = adapter.load_campaigns(p)
    # master off → every campaign off regardless of per-campaign flag
    assert all(not c.enabled for c in defs.values())


def test_load_campaigns_master_and_per_campaign(tmp_path):
    p = _write_cfg(
        tmp_path,
        "enabled: true\ncampaigns:\n  joint_event: true\n  farm_raid: false\n",
    )
    defs = adapter.load_campaigns(p)
    assert defs["joint_event"].enabled is True
    assert defs["farm_raid"].enabled is False
    assert defs["reinforcement"].enabled is False  # absent → false


def test_env_override_kills_master(tmp_path, monkeypatch):
    p = _write_cfg(tmp_path, "enabled: true\ncampaigns: {joint_event: true}\n")
    monkeypatch.setenv("WOS_FLEET_ENABLED", "false")
    assert all(not c.enabled for c in adapter.load_campaigns(p).values())


def test_run_json_round_trip():
    run = CampaignRun(
        campaign_id="farm_raid", run_id="farm_raid:run1", phase_index=1, status=RUNNING,
        participants=(
            Participant("F", "farm", "dev-a"),
            Participant("G", "fighter", "dev-b", shares_device=False),
        ),
        statuses=(
            ParticipantStatus("F", reached=True, last_directive_id="k1"),
            ParticipantStatus("G"),
        ),
        started_at=1.0, phase_started_at=2.0, deadline_at=1800.0,
    )
    assert adapter.run_from_json(adapter.run_to_json(run)) == run


def test_planner_fleet_online_and_signal():
    pf = adapter._PlannerFleet(
        online_fids={"F"},
        signals={"F": {"city_empty": "1"}, "G": {}},
    )
    assert pf.online("F") is True
    assert pf.online("G") is False
    assert pf.signal("F", "city_empty") is True
    assert pf.signal("G", "city_empty") is False


def test_calendar_view():
    win = SimpleNamespace(slug="power_up", active=True, ends_in_s=120.0)
    cv = adapter._CalendarView((win,))
    assert cv.window_active("power_up") is True
    assert cv.ends_in_s("power_up") == 120.0
    assert cv.window_active("other") is False
    assert cv.ends_in_s("other") == float("inf")
