"""Adapters: planner outputs → coordinator candidates, and an end-to-end pass."""
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
    coordinate,
    from_build_slate,
    from_hero_plan,
    from_intel_plan,
    from_pet_plan,
    from_research_plan,
)
from games.wos.core.pets.planner import PetSpec
from games.wos.core.pets.planner import plan_next as pet_plan_next
from games.wos.core.research.planner import load_research_graph, plan_next
from games.wos.heroes.heroes.planner import HeroSpec
from games.wos.heroes.heroes.planner import plan_next as hero_plan_next
from games.wos.intel.planner import IntelEvent
from games.wos.intel.planner import plan_next as intel_plan_next


def _build_graph():
    furnace = BuildingSpec("furnace", "Furnace", (
        LevelReq("1", 1.0, (), (), 0, None),
        LevelReq("2", 2.0, (), (("item_icon_103", 100),), 0, None),
    ))
    return BuildGraph(buildings={"furnace": furnace})


def _build_slate():
    pick = BuildCandidate(
        instance_id="furnace", spec_id="furnace", track="progression",
        to_level="2", to_rank=2.0, value=100.0, cost_total=100, affordable=True, time_s=0,
    )
    return BuildSlate(picks=(pick,), candidates=(pick,), reason="selected")


def test_from_research_plan_builds_a_research_candidate():
    g = load_research_graph()
    plan = plan_next(g, {}, rc_level=30)
    cands = from_research_plan(plan, g)
    assert len(cands) == 1
    c = cands[0]
    assert c.domain == "research"
    assert c.channel_kind == RESEARCH
    assert c.key == plan.step.node_id
    assert c.cost                                   # canonical resource names (meat/wood/…)
    assert c.priority >= 800


def test_from_build_slate_maps_costs_via_item_table():
    cands = from_build_slate(
        _build_slate(), _build_graph(), item_to_resource={"item_icon_103": "wood"},
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.channel_kind == CONSTRUCTION
    assert c.domain == "building_progression"
    assert c.cost == {"wood": 100}


def test_from_build_slate_no_shared_cost_without_item_map():
    cands = from_build_slate(_build_slate(), _build_graph())
    assert cands[0].cost == {}                      # unmapped → doesn't contend on the pool


def test_from_hero_plan_builds_a_hero_candidate():
    cat = {"natalia": HeroSpec("natalia", "Natalia", "Legendary", "Infantry", "Combat", (5,))}
    plan = hero_plan_next(cat, {}, {"shard:natalia": 999, "book:mythic": 999})
    cands = from_hero_plan(plan)
    assert len(cands) == 1
    c = cands[0]
    assert c.domain == "heroes"
    assert c.channel_kind == HERO
    assert c.key.startswith("natalia:")
    assert c.cost                                    # tiered books / per-hero shards


def test_from_pet_plan_builds_a_pet_candidate():
    cat = {"snow_leopard": PetSpec("snow_leopard", "Snow Leopard", "", 140, None, "Lightning Raid", "march")}
    plan = pet_plan_next(cat, {}, {"pet_shard:snow_leopard": 99, "pet_food": 99}, server_days=200)
    cands = from_pet_plan(plan)
    assert len(cands) == 1
    c = cands[0]
    assert c.domain == "pets"
    assert c.channel_kind == PET
    assert c.key.startswith("snow_leopard:")
    assert c.cost


def test_end_to_end_building_and_research_share_the_tick():
    g = load_research_graph()
    research = from_research_plan(plan_next(g, {}, rc_level=30), g)
    building = from_build_slate(
        _build_slate(), _build_graph(), item_to_resource={"item_icon_103": "wood"},
    )
    channels = [Channel("c1", CONSTRUCTION), Channel("r1", RESEARCH)]
    dec = coordinate(channels, [*research, *building], {"wood": 100_000, "meat": 100_000,
                                                        "coal": 100_000, "iron": 100_000, "steel": 100_000})
    kinds = {c.action.channel_kind for c in dec.commits}
    assert kinds == {CONSTRUCTION, RESEARCH}        # both domains run this tick
    assert len(dec.commits) == 2


def test_from_intel_plan_builds_stamina_priced_march_candidates():
    board = [IntelEvent("skull_horned", "gold"), IntelEvent("fight", "purple")]
    plan = intel_plan_next(board, stamina=100, cost_per_event=10)
    cands = from_intel_plan(plan)
    assert len(cands) == 2
    assert all(c.channel_kind == MARCH for c in cands)
    assert all(c.domain == "intel" for c in cands)
    assert all(c.cost == {"stamina": 10} for c in cands)
    # Value order preserved across march slots (gold special first).
    assert cands[0].priority > cands[1].priority
    assert "gold" in cands[0].detail


def test_intel_takes_a_march_slot_before_a_long_gather():
    plan = intel_plan_next([IntelEvent("skull", "gold")], stamina=100, cost_per_event=10)
    intel = from_intel_plan(plan)
    gather = [CandidateAction("gather", MARCH, "gather_coal", 720)]   # boosted gather
    dec = coordinate([Channel("m1", MARCH)], [*intel, *gather], {"stamina": 100})
    assert len(dec.commits) == 1
    assert dec.commits[0].action.domain == "intel"   # quick intel run wins the slot
    assert dec.remaining["stamina"] == 90            # stamina spent from the shared pool


def test_intel_starves_on_the_shared_stamina_pool_when_drained():
    plan = intel_plan_next([IntelEvent("skull", "gold")], stamina=100, cost_per_event=10)
    intel = from_intel_plan(plan)
    dec = coordinate([Channel("m1", MARCH)], intel, {"stamina": 5})
    assert dec.commits == ()
    assert "stamina" in dec.bottleneck_resources
