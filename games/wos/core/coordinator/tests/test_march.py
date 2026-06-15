"""MARCH-channel arbitration: intel vs gather over the idle march slots.

Exercises :func:`coordinator.plan_march` — the composition that routes the intel
batch (via ``from_intel_plan``) and economy gather targets through
:func:`coordinate` for the contended march channel.
"""
from __future__ import annotations

from games.wos.core.coordinator import MARCH, plan_march
from games.wos.intel.planner import IntelEvent, plan_next


def _intel_plan(*, stamina, color="gold", kind="skull_horned"):
    """A one-marker intel plan the planner deemed worth taking."""
    events = [IntelEvent(kind=kind, color=color, score=1.0, x=100, y=200)]
    return plan_next(events, stamina=stamina)


def test_intel_preempts_gather_for_the_slot():
    # Intel (band 760) outranks boosted gather (450×1.6=720): a quick expiring
    # run takes the one free slot before a long gather.
    decision = plan_march(
        idle_slots=1,
        balances={"stamina": 100, "coal": 100},
        intel_plan=_intel_plan(stamina=100),
        min_buffer={"coal": 1000},  # coal short → a gather candidate competes
    )
    committed = decision.committed_for(MARCH)
    assert len(committed) == 1
    assert committed[0].action.domain == "intel"
    assert any(c.domain == "gather" for c in decision.no_channel)


def test_gather_fills_slot_when_no_intel():
    # Board empty / planner declined → the slot goes to the scarce-resource gather.
    decision = plan_march(
        idle_slots=1,
        balances={"stamina": 100, "coal": 100},
        intel_plan=None,
        min_buffer={"coal": 1000},
    )
    committed = decision.committed_for(MARCH)
    assert len(committed) == 1
    assert committed[0].action.domain == "gather"


def test_intel_starved_by_stamina_yields_slot_to_gather():
    # The planner took the marker, but stamina fell to 5 by commit time → intel
    # starves at the coordinator and the (free) gather takes the slot instead;
    # stamina is reported as the bottleneck for the economy loop.
    decision = plan_march(
        idle_slots=1,
        balances={"stamina": 5, "coal": 100},
        intel_plan=_intel_plan(stamina=100),
        min_buffer={"coal": 1000},
    )
    committed = decision.committed_for(MARCH)
    assert len(committed) == 1
    assert committed[0].action.domain == "gather"
    assert any(c.domain == "intel" for c in decision.starved)
    assert "stamina" in decision.bottleneck_resources


def test_two_slots_run_intel_and_gather():
    decision = plan_march(
        idle_slots=2,
        balances={"stamina": 100, "coal": 100},
        intel_plan=_intel_plan(stamina=100),
        min_buffer={"coal": 1000},
    )
    domains = sorted(c.action.domain for c in decision.committed_for(MARCH))
    assert domains == ["gather", "intel"]


def test_no_idle_slots_commits_nothing():
    decision = plan_march(
        idle_slots=0,
        balances={"stamina": 100},
        intel_plan=_intel_plan(stamina=100),
    )
    assert decision.commits == ()
    assert any(c.domain == "intel" for c in decision.no_channel)
