"""adapter.partition_by_safety — the orchestrator's pre-arbitration safety gate."""
from __future__ import annotations

from games.wos.core.fleet.adapter import partition_by_safety
from games.wos.core.fleet.catalog import build_campaign_defs

from coord.campaign import CampaignRun, Participant, ParticipantStatus
from coord.campaign.model import RUNNING

DEFS = build_campaign_defs()


class _Cal:
    def __init__(self, active=()) -> None:
        self._a = set(active)

    def window_active(self, slug):
        return slug in self._a

    def ends_in_s(self, slug):
        return float("inf")


def _run(cid, parts):
    return CampaignRun(
        campaign_id=cid, run_id=f"{cid}:r", phase_index=0, status=RUNNING,
        participants=tuple(parts),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in parts),
        started_at=0.0, phase_started_at=0.0, deadline_at=1000.0,
    )


def test_war_suppresses_raid_keeps_reinforcement():
    pairs = [
        (DEFS["farm_raid"], _run("farm_raid", [Participant("F", "farm", "dev-a"),
                                               Participant("G", "fighter", "dev-b")])),
        (DEFS["reinforcement"], _run("reinforcement", [Participant("H", "helper", "dev-c")])),
    ]
    safe, suppressed = partition_by_safety(pairs, _Cal(active={"alliance_war"}))
    assert [c.id for c, _ in safe] == ["reinforcement"]   # defensive survives
    assert suppressed == [("farm_raid:r", "war_hunt_keep_troops_home")]


def test_event_participant_excluded_from_raid():
    event = (DEFS["joint_event"], _run("joint_event", [Participant("G", "balanced", "dev-b")]))
    raid = (DEFS["farm_raid"], _run("farm_raid", [Participant("F", "farm", "dev-a"),
                                                  Participant("G", "fighter", "dev-b")]))
    safe, suppressed = partition_by_safety([event, raid], _Cal())
    ids = [c.id for c, _ in safe]
    assert "joint_event" in ids and "farm_raid" not in ids
    assert any("participant_in_active_event" in why for _, why in suppressed)


def test_all_allowed_when_calm():
    pairs = [
        (DEFS["farm_raid"], _run("farm_raid", [Participant("F", "farm", "dev-a"),
                                               Participant("G", "fighter", "dev-b")])),
    ]
    safe, suppressed = partition_by_safety(pairs, _Cal())
    assert len(safe) == 1 and suppressed == []
