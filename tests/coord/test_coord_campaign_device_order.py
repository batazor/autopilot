"""The device scheduler's order drives the planner's per-directive sequence_order."""
from __future__ import annotations

from coord.campaign import (
    ALL_REACHED,
    CampaignDef,
    CampaignRun,
    Participant,
    ParticipantStatus,
    Phase,
    PhaseBarrier,
    Step,
    plan_campaign_tick,
)
from coord.campaign.model import RUNNING


class _Fleet:
    def __init__(self, online) -> None:
        self._online = set(online)

    def online(self, fid):
        return fid in self._online

    def signal(self, fid, name):
        return False


class _Cal:
    def window_active(self, slug):
        return False

    def ends_in_s(self, slug):
        return float("inf")


CDEF = CampaignDef(
    id="ev", title="", trigger="manual",
    phases=(
        Phase("p", (Step("run_scenario", "all", "s", requires_switch=True),),
              PhaseBarrier(ALL_REACHED, signal="q", timeout_s=10_000)),
    ),
)
PARTS = [Participant("A", "x", "dev-a", shares_device=True),
         Participant("B", "x", "dev-a", shares_device=True)]


def _run():
    return CampaignRun(
        campaign_id="ev", run_id="r", phase_index=0, status=RUNNING,
        participants=tuple(PARTS),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in PARTS),
        started_at=0.0, phase_started_at=0.0, deadline_at=10_000.0,
    )


def test_sequence_order_follows_device_order():
    fleet = _Fleet({"A", "B"})
    dec = plan_campaign_tick(CDEF, _run(), fleet, _Cal(), now=0.0, device_order={"B": 0, "A": 1})
    seq = {d.fid: d.sequence_order for d in dec.directives}
    assert seq == {"B": 0, "A": 1}            # scheduler put B first


def test_sequence_order_falls_back_to_emission_order():
    fleet = _Fleet({"A", "B"})
    dec = plan_campaign_tick(CDEF, _run(), fleet, _Cal(), now=0.0)
    seq = {d.fid: d.sequence_order for d in dec.directives}
    assert seq == {"A": 0, "B": 1}            # no hint → emission order
