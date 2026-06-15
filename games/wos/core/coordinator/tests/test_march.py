"""MARCH-channel arbitration: intel vs gather over the idle march slots.

Exercises :func:`coordinator.plan_march` — the composition that routes the intel
batch (via ``from_intel_plan``) and economy gather targets through
:func:`coordinate` for the contended march channel.
"""
from __future__ import annotations

from games.wos.core.coordinator import (
    MARCH,
    intel_intent,
    plan_march,
    timed_event_intent,
)
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


# --- intel_intent: the blind "intel wants a slot" candidate ------------------


def test_intel_intent_emits_march_candidate_when_affordable():
    c = intel_intent(stamina=100, seconds_since_last_run=None, cost=10)
    assert c is not None
    assert c.domain == "intel"
    assert c.channel_kind == MARCH
    assert c.cost == {"stamina": 10}


def test_intel_intent_none_without_stamina():
    assert intel_intent(stamina=None, seconds_since_last_run=None) is None


def test_intel_intent_none_below_cost():
    assert intel_intent(stamina=5, seconds_since_last_run=None, cost=10) is None


def test_intel_intent_reserve_blocks_then_clears():
    # 56 stamina, 50 reserved for Joe → 6 spendable < 10.
    assert intel_intent(stamina=56, seconds_since_last_run=None, cost=10, reserve=50) is None
    assert intel_intent(stamina=56, seconds_since_last_run=None, cost=10, reserve=0) is not None


def test_intel_intent_cooldown_blocks_then_clears():
    assert intel_intent(
        stamina=100, seconds_since_last_run=100, cost=10, cooldown_s=900
    ) is None
    assert intel_intent(
        stamina=100, seconds_since_last_run=1000, cost=10, cooldown_s=900
    ) is not None


# --- timed_event_intent: a generic time-limited march-spending event ---------


def test_timed_event_intent_active_with_attempts():
    c = timed_event_intent("romance_season", active=True, attempts_left=5)
    assert c is not None
    assert c.domain == "romance_season"
    assert c.channel_kind == MARCH
    assert c.cost == {}  # spends a march slot, not the shared resource pool


def test_timed_event_intent_skips_when_inactive():
    assert timed_event_intent("romance_season", active=False, attempts_left=5) is None


def test_timed_event_intent_skips_when_attempts_exhausted():
    assert timed_event_intent("romance_season", active=True, attempts_left=0) is None


def test_timed_event_intent_allows_unknown_attempts():
    # Never read yet (None) → optimistically run so the scenario can read it.
    assert timed_event_intent("romance_season", active=True, attempts_left=None) is not None


def test_timed_event_intent_banded_below_intel_above_gather():
    romance = timed_event_intent("romance_season", active=True, attempts_left=5)
    intel = intel_intent(stamina=100, seconds_since_last_run=None)
    assert intel is not None and romance is not None
    assert intel.priority > romance.priority > 450  # gather base
