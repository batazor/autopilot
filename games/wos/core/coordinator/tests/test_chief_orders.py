"""Chief's House order selection tied to events / situation."""
from __future__ import annotations

from games.wos.core.coordinator import recommend_orders
from games.wos.core.coordinator.chief_orders import (
    COMPREHENSIVE_CARE,
    DEFAULT_ORDER,
    DOUBLE_TIME,
    PRODUCTIVITY,
    RUSH_JOB,
    URGENT_MOBILIZATION,
)


def test_construction_event_leads_with_rush_job():
    plan = recommend_orders(active_categories=["construction"])
    assert plan.recommended[0] == RUSH_JOB
    assert "construction" in plan.reasons[RUSH_JOB]


def test_training_event_leads_with_urgent_mobilization():
    plan = recommend_orders(active_categories=["training"])
    assert plan.recommended[0] == URGENT_MOBILIZATION


def test_research_event_leads_with_double_time():
    plan = recommend_orders(active_categories=["research"])
    assert plan.recommended[0] == DOUBLE_TIME


def test_injured_troops_prioritise_comprehensive_care():
    plan = recommend_orders(injured=1500)
    assert plan.recommended[0] == COMPREHENSIVE_CARE


def test_pvp_window_prioritises_comprehensive_care():
    plan = recommend_orders(pvp_window=True)
    assert COMPREHENSIVE_CARE in plan.recommended[:1]


def test_any_power_event_pulls_build_train_research_orders_up():
    plan = recommend_orders(active_categories=["any_power"])
    top3 = set(plan.recommended[:3])
    assert {RUSH_JOB, URGENT_MOBILIZATION, DOUBLE_TIME} == top3


def test_no_signal_uses_default_combo():
    plan = recommend_orders()
    assert plan.recommended == DEFAULT_ORDER
    assert plan.reasons == {}


def test_recommendation_is_complete_and_deduped():
    plan = recommend_orders(active_categories=["construction", "gather"], injured=10)
    assert set(plan.recommended) == set(DEFAULT_ORDER)         # every order present
    assert len(plan.recommended) == len(set(plan.recommended))  # no dupes
    assert plan.recommended[0] == RUSH_JOB                      # construction-tied leads
    assert PRODUCTIVITY in plan.reasons                         # gather event noted
