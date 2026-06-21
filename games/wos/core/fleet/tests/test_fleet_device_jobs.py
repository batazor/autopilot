"""Build device schedules from a run's shared-device participants."""
from __future__ import annotations

from games.wos.core.fleet.device_jobs import optimized_device_order, schedules_by_device

from coord.campaign import CampaignRun, Participant, ParticipantStatus
from coord.campaign.model import RUNNING


def _run(parts, *, now=0.0, ttl=10_000.0):
    return CampaignRun(
        campaign_id="joint_event", run_id="r", phase_index=0, status=RUNNING,
        participants=tuple(parts),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in parts),
        started_at=now, phase_started_at=now, deadline_at=now + ttl,
    )


def test_single_account_devices_are_omitted():
    run = _run([Participant("A", "x", "dev-a"), Participant("B", "x", "dev-b")])
    # each on its own device → nothing to sequence
    assert optimized_device_order(run, now=0.0) == {}


def test_shared_device_orders_by_value():
    run = _run([Participant("G1", "x", "dev-a"), Participant("G2", "x", "dev-a")])
    # G2 is worth more → serviced first (rank 0)
    order = optimized_device_order(run, now=0.0, value_of=lambda p: 5.0 if p.fid == "G2" else 1.0)
    assert order == {"G2": 0, "G1": 1}


def test_tight_window_drops_low_value_account():
    # window only fits one (switch 20 + service 60 = 80 ≤ 100; second = 160 > 100)
    run = _run([Participant("G1", "x", "dev-a"), Participant("G2", "x", "dev-a")], ttl=100.0)
    scheds = schedules_by_device(
        run, now=0.0, value_of=lambda p: 5.0 if p.fid == "G2" else 1.0
    )
    sched = scheds["dev-a"]
    assert sched.order == ("G2",)       # keep the valuable one
    assert sched.dropped == ("G1",)
    # dropped still gets a (last) rank so the planner sequences it after
    assert optimized_device_order(
        run, now=0.0, value_of=lambda p: 5.0 if p.fid == "G2" else 1.0
    ) == {"G2": 0, "G1": 1}


def test_three_on_one_device_all_fit_when_window_ample():
    run = _run([Participant(f"G{i}", "x", "dev-a") for i in range(3)])
    order = optimized_device_order(run, now=0.0, service_of=lambda _p: 10.0, switch_s=2.0)
    assert set(order) == {"G0", "G1", "G2"}
    assert sorted(order.values()) == [0, 1, 2]
