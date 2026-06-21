"""Raid ROI → priority multiplier feeding the arbiter (the B↔A connection)."""
from __future__ import annotations

from games.wos.core.fleet import objective
from games.wos.core.fleet.adapter import build_claim
from games.wos.core.fleet.catalog import build_campaign_defs

from coord.campaign import CampaignRun, Participant, ParticipantStatus, arbitrate
from coord.campaign.model import RUNNING

DEFS = build_campaign_defs()


def _run(cid, parts, *, ttl=1000.0):
    return CampaignRun(
        campaign_id=cid, run_id=f"{cid}:r", phase_index=0, status=RUNNING,
        participants=tuple(parts),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in parts),
        started_at=0.0, phase_started_at=0.0, deadline_at=ttl,
    )


def test_value_factor_scales_priority():
    run = _run("farm_raid", [Participant("F", "farm", "dev-a")])
    base = objective.campaign_priority(DEFS["farm_raid"], run, 0.0)
    lifted = objective.campaign_priority(DEFS["farm_raid"], run, 0.0, value_factor=2.0)
    assert lifted == base * 2.0


def test_raid_value_factor_mapping():
    assert objective.raid_value_factor(0.0) == 1.0
    assert objective.raid_value_factor(-50.0) == 1.0          # not worth it → no lift
    assert 1.0 < objective.raid_value_factor(500.0) < objective.VALUE_FACTOR_CAP
    assert objective.raid_value_factor(10_000.0) == objective.VALUE_FACTOR_CAP  # capped


def test_high_roi_raid_preempts_event_sharing_account():
    # raid (band 500) and a joint-event (band 600) both want fighter G on dev-b
    raid_run = _run("farm_raid", [Participant("F", "farm", "dev-a"),
                                  Participant("G", "fighter", "dev-b")])
    event_run = _run("joint_event", [Participant("G", "balanced", "dev-b")])

    # baseline: event outranks raid → event wins the shared account
    baseline = arbitrate([
        build_claim(DEFS["farm_raid"], raid_run, 0.0),
        build_claim(DEFS["joint_event"], event_run, 0.0),
    ])
    assert baseline.active == ("joint_event:r",)

    # a fat farm (ROI lift 1.5×) flips it: raid 500×1.5=750 > event 600
    lifted = arbitrate([
        build_claim(DEFS["farm_raid"], raid_run, 0.0, value_factor=1.5),
        build_claim(DEFS["joint_event"], event_run, 0.0),
    ])
    assert "farm_raid:r" in lifted.active
    assert "joint_event:r" in lifted.starved
