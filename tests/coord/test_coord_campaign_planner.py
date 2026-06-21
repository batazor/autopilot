"""Pure campaign-planner tests for all three reference campaigns.

Synthetic fleet + calendar (the Protocol seams make this trivial — no Redis).
Proves the single Campaign→Phase→Step+Barrier model expresses joint-event,
farm-raid (incl. the abort-when-city_empty-never-set safety gate) and
reinforcement.
"""
from __future__ import annotations

from coord.campaign import (
    ABORT,
    ADVANCE,
    ALL_REACHED,
    ANY_REACHED,
    DEADLINE_ONLY,
    FLAG_SET,
    TRIGGER_CALENDAR,
    TRIGGER_MANUAL,
    TRIGGER_NOTIFY,
    CampaignDef,
    CampaignRun,
    Participant,
    ParticipantStatus,
    Phase,
    PhaseBarrier,
    Step,
    plan_campaign_tick,
)
from coord.campaign.model import ABORTED, DONE, RUNNING


# --- fakes -------------------------------------------------------------------
class FakeFleet:
    def __init__(self, online=(), flags=None) -> None:
        self._online = set(online)
        self._flags = dict(flags or {})

    def online(self, fid):
        return fid in self._online

    def signal(self, fid, name):
        return bool(self._flags.get((fid, name), False))


class FakeCal:
    def __init__(self, active=()) -> None:
        self._active = set(active)

    def window_active(self, slug):
        return slug in self._active

    def ends_in_s(self, slug):
        return 999.0 if slug in self._active else float("inf")


def make_run(cdef, participants, *, phase=0, status=RUNNING, now=0.0, ttl=100_000.0,
             phase_started=0.0, statuses=None):
    sts = statuses or tuple(ParticipantStatus(fid=p.fid) for p in participants)
    return CampaignRun(
        campaign_id=cdef.id, run_id="run1", phase_index=phase, status=status,
        participants=tuple(participants), statuses=sts, started_at=now,
        phase_started_at=phase_started, deadline_at=now + ttl,
    )


# --- campaign defs (mirror the WoS catalog shape) ----------------------------
JOINT = CampaignDef(
    id="joint_event", title="Joint event", trigger=TRIGGER_CALENDAR,
    anchor_event_slug="power_up", min_participants=2,
    phases=(
        Phase("gather", (Step("run_scenario", "all", "event.gather", requires_switch=True),),
              PhaseBarrier(ALL_REACHED, signal="quota_reached", timeout_s=3600, on_timeout=ADVANCE)),
        Phase("converge", (Step("run_scenario", "all", "event.rally", requires_switch=True),),
              PhaseBarrier(ALL_REACHED, signal="joined", timeout_s=900, on_timeout=ADVANCE)),
        Phase("claim", (Step("run_scenario", "all", "event.claim", requires_switch=True),),
              PhaseBarrier(DEADLINE_ONLY, timeout_s=300, on_timeout=ADVANCE)),
    ),
)

RAID = CampaignDef(
    id="farm_raid", title="Farm raid", trigger=TRIGGER_MANUAL,
    min_participants=2, max_participants=2,
    phases=(
        Phase("farm_recall", (Step("recall", "farm", "city.recall_troops"),),
              PhaseBarrier(FLAG_SET, signal="city_empty", timeout_s=600, on_timeout=ABORT),
              rollback=(Step("run_scenario", "farm", "city.resume_troops"),)),
        Phase("fighter_attack",
              (Step("attack_coords", "fighter", "march.attack_coords",
                    requires_switch=True, params={"target": "farm_city"}),),
              PhaseBarrier(FLAG_SET, signal="attack_landed", timeout_s=300, on_timeout=ABORT),
              rollback=(Step("run_scenario", "farm", "city.resume_troops"),)),
        Phase("farm_resume", (Step("run_scenario", "farm", "city.resume_troops"),),
              PhaseBarrier(DEADLINE_ONLY, timeout_s=120, on_timeout=ADVANCE)),
    ),
)

REINFORCE = CampaignDef(
    id="reinforcement", title="Reinforce", trigger=TRIGGER_NOTIFY,
    min_participants=1, max_participants=2, default_ttl_s=600,
    phases=(
        Phase("send",
              (Step("reinforce", "helper", "march.reinforce",
                    requires_switch=True, params={"target": "ally_under_attack"}),),
              PhaseBarrier(ANY_REACHED, signal="troops_sent", timeout_s=120, on_timeout=ADVANCE)),
    ),
)

JOINT_PARTS = [
    Participant("111", "balanced", "dev-a", shares_device=True),
    Participant("222", "balanced", "dev-a", shares_device=True),
    Participant("333", "balanced", "dev-b"),
]
RAID_PARTS = [Participant("F", "farm", "dev-a"), Participant("G", "fighter", "dev-b")]
REINFORCE_PARTS = [Participant("H", "helper", "dev-c")]


# --- joint event -------------------------------------------------------------
def test_joint_phase0_emits_with_shared_device_sequencing():
    fleet = FakeFleet(online={"111", "222", "333"})
    run = make_run(JOINT, JOINT_PARTS)
    dec = plan_campaign_tick(JOINT, run, fleet, FakeCal({"power_up"}), now=10.0)
    assert dec.advance_to is None
    assert {d.fid for d in dec.directives} == {"111", "222", "333"}
    by_fid = {d.fid: d for d in dec.directives}
    # the two accounts sharing dev-a are sequenced (group=dev-a, orders 0 and 1)
    assert by_fid["111"].sequence_group == "dev-a"
    assert by_fid["222"].sequence_group == "dev-a"
    assert {by_fid["111"].sequence_order, by_fid["222"].sequence_order} == {0, 1}
    # the account on its own device is its own group
    assert by_fid["333"].sequence_group == "dev-b"
    assert all(d.kind == "run_scenario" and d.scenario == "event.gather" for d in dec.directives)


def test_joint_advances_when_all_reach_quota():
    fleet = FakeFleet(
        online={"111", "222", "333"},
        flags={("111", "quota_reached"): True, ("222", "quota_reached"): True,
               ("333", "quota_reached"): True},
    )
    dec = plan_campaign_tick(JOINT, make_run(JOINT, JOINT_PARTS), fleet,
                             FakeCal({"power_up"}), now=10.0)
    assert dec.advance_to == 1
    assert dec.next_status == RUNNING


def test_joint_holds_when_partial_quota():
    fleet = FakeFleet(online={"111", "222", "333"},
                      flags={("111", "quota_reached"): True})
    dec = plan_campaign_tick(JOINT, make_run(JOINT, JOINT_PARTS), fleet,
                             FakeCal({"power_up"}), now=10.0)
    assert dec.advance_to is None


def test_joint_timeout_advances_best_effort():
    fleet = FakeFleet(online={"111", "222", "333"})
    # now past the 3600s phase timeout, quota not met → on_timeout=ADVANCE
    dec = plan_campaign_tick(JOINT, make_run(JOINT, JOINT_PARTS), fleet,
                             FakeCal({"power_up"}), now=4000.0)
    assert dec.advance_to == 1
    assert "timed_out_advance" in dec.trace


def test_joint_holds_when_participant_offline():
    fleet = FakeFleet(online={"111", "222"})  # 333 offline
    dec = plan_campaign_tick(JOINT, make_run(JOINT, JOINT_PARTS), fleet,
                             FakeCal({"power_up"}), now=10.0)
    assert dec.directives == ()
    assert any(t.startswith("hold_offline") for t in dec.trace)


def test_joint_calendar_inactive_noop_before_start():
    fleet = FakeFleet(online={"111", "222", "333"})
    dec = plan_campaign_tick(JOINT, make_run(JOINT, JOINT_PARTS), fleet, FakeCal(), now=10.0)
    assert dec.directives == ()
    assert "calendar_window_inactive" in dec.trace


def test_joint_calendar_closed_mid_run_aborts():
    fleet = FakeFleet(online={"111", "222", "333"})
    run = make_run(JOINT, JOINT_PARTS, phase=1)  # already converging
    dec = plan_campaign_tick(JOINT, run, fleet, FakeCal(), now=10.0)
    assert dec.next_status == ABORTED


# --- farm raid ---------------------------------------------------------------
def test_raid_phase0_emits_only_farm_recall():
    fleet = FakeFleet(online={"F", "G"})
    dec = plan_campaign_tick(RAID, make_run(RAID, RAID_PARTS), fleet, FakeCal(), now=10.0)
    assert len(dec.directives) == 1
    d = dec.directives[0]
    assert d.fid == "F" and d.kind == "recall" and d.scenario == "city.recall_troops"
    # the fighter is idle until the safety gate opens
    assert all(x.fid != "G" for x in dec.directives)


def test_raid_advances_when_city_empty():
    fleet = FakeFleet(online={"F", "G"}, flags={("F", "city_empty"): True})
    dec = plan_campaign_tick(RAID, make_run(RAID, RAID_PARTS), fleet, FakeCal(), now=10.0)
    assert dec.advance_to == 1


def test_raid_aborts_and_rolls_back_when_city_never_empties():
    fleet = FakeFleet(online={"F", "G"})  # city_empty never set
    run = make_run(RAID, RAID_PARTS, phase_started=0.0)
    dec = plan_campaign_tick(RAID, run, fleet, FakeCal(), now=700.0)  # past 600s timeout
    assert dec.next_status == ABORTED
    # rollback fires: farm resumes its troops...
    assert any(d.scenario == "city.resume_troops" and d.fid == "F" for d in dec.directives)
    # ...and the fighter NEVER got an attack directive (the gate held)
    assert all(d.kind != "attack_coords" for d in dec.directives)


def test_raid_attack_phase_requires_switch_and_advances():
    fleet = FakeFleet(online={"F", "G"})
    run = make_run(RAID, RAID_PARTS, phase=1)
    dec = plan_campaign_tick(RAID, run, fleet, FakeCal(), now=10.0)
    assert len(dec.directives) == 1
    d = dec.directives[0]
    assert d.fid == "G" and d.kind == "attack_coords"
    assert d.requires_switch is True and d.sequence_group == "dev-b"
    assert d.params.get("target") == "farm_city"

    # once the attack lands, advance to farm_resume (phase 2)
    landed = FakeFleet(online={"F", "G"}, flags={("G", "attack_landed"): True})
    dec2 = plan_campaign_tick(RAID, run, landed, FakeCal(), now=10.0)
    assert dec2.advance_to == 2


# --- reinforcement -----------------------------------------------------------
def test_reinforce_emits_helper_directive():
    fleet = FakeFleet(online={"H"})
    dec = plan_campaign_tick(REINFORCE, make_run(REINFORCE, REINFORCE_PARTS, ttl=600.0),
                             fleet, FakeCal(), now=1.0)
    assert len(dec.directives) == 1
    d = dec.directives[0]
    assert d.fid == "H" and d.kind == "reinforce" and d.requires_switch is True


def test_reinforce_done_when_any_sent():
    fleet = FakeFleet(online={"H"}, flags={("H", "troops_sent"): True})
    dec = plan_campaign_tick(REINFORCE, make_run(REINFORCE, REINFORCE_PARTS, ttl=600.0),
                             fleet, FakeCal(), now=1.0)
    assert dec.next_status == DONE


# --- run-level guards --------------------------------------------------------
def test_run_deadline_aborts():
    fleet = FakeFleet(online={"F", "G"})
    run = make_run(RAID, RAID_PARTS, now=0.0, ttl=100.0)
    dec = plan_campaign_tick(RAID, run, fleet, FakeCal(), now=200.0)
    assert dec.next_status == ABORTED
    assert "run_deadline_exceeded" in dec.trace


def test_terminal_run_is_noop():
    fleet = FakeFleet(online={"H"})
    run = make_run(REINFORCE, REINFORCE_PARTS, status=DONE)
    dec = plan_campaign_tick(REINFORCE, run, fleet, FakeCal(), now=1.0)
    assert dec.next_status == DONE
    assert dec.directives == ()
    assert dec.trace == ("terminal",)


def test_idempotent_no_reemit_when_in_flight():
    fleet = FakeFleet(online={"F", "G"})
    run = make_run(RAID, RAID_PARTS)
    first = plan_campaign_tick(RAID, run, fleet, FakeCal(), now=10.0)
    # feed the updated statuses back (directive recorded as in-flight)
    run2 = make_run(RAID, RAID_PARTS, statuses=first.updated_statuses)
    second = plan_campaign_tick(RAID, run2, fleet, FakeCal(), now=11.0)
    assert second.directives == ()  # already in flight → no duplicate
