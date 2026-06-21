"""The unified tick: every factor folds into one cross-channel decision."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BuildCandidate,
    BuildGraph,
    BuildingSpec,
    BuildSlate,
    LevelReq,
)
from games.wos.core.coordinator import (
    CONSTRUCTION,
    HERO,
    MARCH,
    PET,
    RESEARCH,
    CandidateAction,
    Channel,
    DailyTask,
    EventWindow,
    FeedbackState,
    Outcome,
    ThreatState,
    plan_cycle,
    record_many,
)
from games.wos.core.coordinator.safety import SHIELD_UP
from games.wos.core.pets.planner import PetSpec
from games.wos.core.pets.planner import plan_next as pet_plan_next
from games.wos.core.research.planner import load_research_graph
from games.wos.core.research.planner import plan_next as research_plan_next
from games.wos.heroes.heroes.planner import HeroSpec
from games.wos.heroes.heroes.planner import plan_next as hero_plan_next

# Plenty of base resources so research never starves on the shared pool.
_BAL = {"meat": 10**9, "wood": 10**9, "coal": 10**9, "iron": 10**9, "steel": 10**9}


def _graph() -> BuildGraph:
    furnace = BuildingSpec("furnace", "Furnace", (
        LevelReq("1", 1.0, (), (), 0, None),
        LevelReq("2", 2.0, (), (("item_icon_103", 100),), 0, None),
    ))
    coal = BuildingSpec("coal_mine", "Coal Mine", (
        LevelReq("1", 1.0, (), (), 0, None),
        LevelReq("2", 2.0, (), (), 0, None),
    ))
    return BuildGraph(buildings={"furnace": furnace, "coal_mine": coal})


def _furnace_slate() -> BuildSlate:
    pick = BuildCandidate(
        instance_id="furnace", spec_id="furnace", track="progression",
        to_level="2", to_rank=2.0, value=100.0, cost_total=100, affordable=True, time_s=0,
    )
    return BuildSlate(picks=(pick,), candidates=(pick,), reason="selected")


def _coal_slate() -> BuildSlate:
    pick = BuildCandidate(
        instance_id="coal_mine", spec_id="coal_mine", track="economy",
        to_level="2", to_rank=2.0, value=55.0, cost_total=0, affordable=True, time_s=0,
    )
    return BuildSlate(picks=(pick,), candidates=(pick,), reason="selected")


def _cand(plan, key):
    return next(c for c in plan.candidates if c.key == key)


def test_no_factors_is_plain_arbitration():
    """No event / quest / threat / feedback → building + research both run."""
    rg = load_research_graph()
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION), Channel("r1", RESEARCH)],
        balances=_BAL,
        build_slate=_furnace_slate(), build_graph=_graph(),
        research_plan=research_plan_next(rg, {}, rc_level=30), research_graph=rg,
    )
    kinds = {c.action.channel_kind for c in plan.decision.commits}
    assert kinds == {CONSTRUCTION, RESEARCH}
    assert plan.boosts == {}                       # no schedule / quest lift
    assert plan.safety.safe_mode is False


def test_all_development_channels_share_one_tick():
    """The headline: building + research + hero + pet, each on its own channel."""
    rg = load_research_graph()
    rplan = research_plan_next(rg, {}, rc_level=30)
    hplan = hero_plan_next(
        {"natalia": HeroSpec("natalia", "Natalia", "Legendary", "Infantry", "Combat", (5,))},
        {}, {"shard:natalia": 999, "book:mythic": 999},
    )
    pplan = pet_plan_next(
        {"snow_leopard": PetSpec("snow_leopard", "Snow Leopard", "", 140, None, "Lightning Raid", "march")},
        {}, {"pet_shard:snow_leopard": 99, "pet_food": 99}, server_days=200,
    )
    balances = {**_BAL, **dict(hplan.step.cost), **dict(pplan.step.cost)}
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION), Channel("r1", RESEARCH),
                  Channel("h1", HERO), Channel("p1", PET)],
        balances=balances,
        build_slate=_furnace_slate(), build_graph=_graph(),
        research_plan=rplan, research_graph=rg,
        hero_plan=hplan, pet_plan=pplan,
    )
    kinds = {c.action.channel_kind for c in plan.decision.commits}
    assert kinds == {CONSTRUCTION, RESEARCH, HERO, PET}
    assert len(plan.decision.commits) == 4


def test_calendar_event_boosts_its_reward_domains():
    """A live Power Up event lifts the any-power domains (incl. research)."""
    rg = load_research_graph()
    plan = plan_cycle(
        channels=[Channel("r1", RESEARCH)], balances=_BAL,
        research_plan=research_plan_next(rg, {}, rc_level=30), research_graph=rg,
        event_windows=[EventWindow(slug="power_up", active=True)],
    )
    assert plan.boosts["research"] == 1.5
    assert "any_power" in plan.calendar.active_categories
    assert _cand(plan, plan.decision.commits[0].action.key).priority == 900.0 * 1.5


def test_open_daily_quest_boosts_its_domain():
    """An open 'build' daily lifts the building domains (the quest-priority path)."""
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION)], balances=_BAL,
        build_slate=_furnace_slate(), build_graph=_graph(),
        daily_tasks=[DailyTask(id="d1", category="build", target=1, progress=0)],
    )
    assert plan.boosts["building_progression"] == 1.3
    assert _cand(plan, "furnace").priority == 850.0 * 1.3


def test_calendar_and_daily_boosts_merge_by_max():
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION)], balances=_BAL,
        build_slate=_furnace_slate(), build_graph=_graph(),
        event_windows=[EventWindow(slug="power_up", active=True)],   # building_progression → 1.5
        daily_tasks=[DailyTask(id="d1", category="build", target=1, progress=0)],  # → 1.3
    )
    assert plan.boosts["building_progression"] == 1.5                # max(1.5, 1.3)


def test_quest_claims_and_one_shot_nudges_surface():
    plan = plan_cycle(
        channels=[], balances=_BAL,
        daily_tasks=[
            DailyTask(id="done", category="build", target=1, progress=1, claimable=True),
            DailyTask(id="rec", category="recruit", target=1, progress=0),
        ],
    )
    assert "done" in plan.daily.claims
    assert any(n.category == "recruit" for n in plan.daily.nudges)


def test_threat_suppresses_exposing_domains_and_raises_shield():
    """In danger: gather is dropped from the channel plan, a shield action is queued."""
    gather = CandidateAction("gather", MARCH, "gather_coal", 720.0)
    plan = plan_cycle(
        channels=[Channel("m1", MARCH)], balances={"stamina": 100},
        extra_candidates=[gather],
        threat=ThreatState(incoming_attack=True),
    )
    assert all(c.domain != "gather" for c in plan.candidates)        # suppressed pre-allocation
    assert plan.decision.commits == ()
    assert plan.safety.safe_mode is True
    assert any(a.kind == SHIELD_UP for a in plan.safety.actions)


def test_feedback_backs_off_a_stuck_action():
    """An action that stalled 3 ticks in a row gets its priority penalised."""
    fb = record_many(FeedbackState(), [Outcome("furnace", "building_progression", False)] * 3)
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION)], balances=_BAL,
        build_slate=_furnace_slate(), build_graph=_graph(),
        feedback_state=fb,
    )
    assert "furnace" in plan.feedback.stuck
    assert _cand(plan, "furnace").priority == 850.0 * 0.3            # base × backoff factor


def test_short_resource_lifts_its_producer_building():
    """Economy bias: coal short → the coal_mine construction candidate is boosted."""
    plan = plan_cycle(
        channels=[Channel("c1", CONSTRUCTION)], balances=_BAL,
        build_slate=_coal_slate(), build_graph=_graph(),
        bottleneck=["coal"],
    )
    assert "coal" in plan.economy.short_resources
    assert plan.economy.producer_boost == {"coal_mine": 1.4}
    assert _cand(plan, "coal_mine").priority == 520.0 * 1.4          # building_economy band × lift
