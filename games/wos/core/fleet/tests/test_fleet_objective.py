"""Campaign priority (bands/urgency/incumbency) + the arbitration preemption."""
from __future__ import annotations

from games.wos.core.fleet import objective
from games.wos.core.fleet.adapter import build_claim
from games.wos.core.fleet.catalog import build_campaign_defs

from coord.campaign import (
    CampaignRun,
    Participant,
    ParticipantStatus,
    arbitrate,
)
from coord.campaign.model import RUNNING

DEFS = build_campaign_defs()


def _run(campaign_id, parts, *, phase=0, started=0.0, ttl=1000.0):
    return CampaignRun(
        campaign_id=campaign_id, run_id=f"{campaign_id}:r", phase_index=phase,
        status=RUNNING, participants=tuple(parts),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in parts),
        started_at=started, phase_started_at=started, deadline_at=started + ttl,
    )


# --- urgency -----------------------------------------------------------------
def test_urgency_rises_to_deadline():
    run = _run("farm_raid", [Participant("F", "farm", "dev-a")], started=0.0, ttl=100.0)
    assert objective.urgency(run, 0.0) == 1.0
    assert objective.urgency(run, 50.0) == 1.5
    assert objective.urgency(run, 100.0) == objective.URGENCY_MAX


# --- bands -------------------------------------------------------------------
def test_band_ordering_reinforce_gt_event_gt_raid():
    p = [Participant("X", "helper", "dev-a")]
    now = 0.0
    pri_reinforce = objective.campaign_priority(DEFS["reinforcement"], _run("reinforcement", p), now)
    pri_event = objective.campaign_priority(DEFS["joint_event"], _run("joint_event", p), now)
    pri_raid = objective.campaign_priority(DEFS["farm_raid"], _run("farm_raid", p), now)
    assert pri_reinforce > pri_event > pri_raid


# --- incumbency --------------------------------------------------------------
def test_incumbency_boosts_in_progress_run():
    p = [Participant("F", "farm", "dev-a")]
    fresh = objective.campaign_priority(DEFS["farm_raid"], _run("farm_raid", p, phase=0), 0.0)
    midway = objective.campaign_priority(DEFS["farm_raid"], _run("farm_raid", p, phase=1), 0.0)
    assert midway == fresh * objective.INCUMBENCY_BONUS


# --- the headline behaviour: reinforcement preempts a raid sharing a fighter --
def test_reinforcement_preempts_raid_sharing_fighter():
    # raid uses farm F (dev-a) + fighter G (dev-b); reinforcement also needs G.
    raid_run = _run("farm_raid", [Participant("F", "farm", "dev-a"),
                                  Participant("G", "fighter", "dev-b")], ttl=1800.0)
    reinforce_run = _run("reinforcement", [Participant("G", "helper", "dev-b")], ttl=600.0)

    claims = [
        build_claim(DEFS["farm_raid"], raid_run, 0.0),
        build_claim(DEFS["reinforcement"], reinforce_run, 0.0),
    ]
    result = arbitrate(claims)
    assert result.active == ("reinforcement:r",)
    assert result.starved == ("farm_raid:r",)
    assert "account:G" in result.contended  # the shared fighter is the bottleneck


def test_disjoint_campaigns_both_run():
    # an event on dev-a accounts and a raid on dev-b/dev-c accounts don't collide
    event_run = _run("joint_event", [Participant("1", "balanced", "dev-a")])
    raid_run = _run("farm_raid", [Participant("F", "farm", "dev-b"),
                                  Participant("G", "fighter", "dev-c")])
    claims = [
        build_claim(DEFS["joint_event"], event_run, 0.0),
        build_claim(DEFS["farm_raid"], raid_run, 0.0),
    ]
    result = arbitrate(claims)
    assert set(result.active) == {"joint_event:r", "farm_raid:r"}
    assert result.contended == ()
