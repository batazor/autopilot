"""Closed economy loop: gather + producer-boost the scarce resource, flush the full."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BuildCandidate,
    BuildGraph,
    BuildingSpec,
    BuildSlate,
    LevelReq,
)
from games.wos.core.coordinator import (
    MARCH,
    Channel,
    coordinate,
    domain_priority,
    economy_bias,
    from_build_slate,
    gather_candidates,
)
from games.wos.core.roles import get_role


def test_bottleneck_drives_gather_and_producer_boost():
    bias = economy_bias({"coal": 5, "wood": 9000}, bottleneck=["coal"])
    assert bias.short_resources == ("coal",)
    assert bias.gather_targets == ("coal",)
    assert bias.gather_boost > 1.0
    assert bias.producer_boost == {"coal_mine": 1.4}     # upgrade the coal producer


def test_neediest_resource_is_gathered_first():
    bias = economy_bias({"coal": 50, "iron": 5}, bottleneck=["coal", "iron"])
    assert bias.gather_targets[0] == "iron"              # most depleted leads


def test_min_buffer_is_proactive():
    bias = economy_bias({"wood": 100}, min_buffer={"wood": 500})
    assert "wood" in bias.short_resources               # below floor → top up


def test_overflow_resource_is_not_gathered():
    bias = economy_bias({"meat": 95}, bottleneck=["meat"], caps={"meat": 100})
    assert "meat" in bias.overflow_resources
    assert bias.gather_targets == ()                     # near cap → spend, don't gather
    assert bias.gather_boost == 1.0


def test_farm_ignores_overflow_cap_and_keeps_gathering():
    # A farm's storehouse cap is deliberately tiny, so overflow must NOT throttle
    # its gathering — the pile is the product, raidable and growing.
    farm = get_role("farm")
    bias = economy_bias({"meat": 95}, bottleneck=["meat"], caps={"meat": 100}, role=farm)
    assert bias.overflow_resources == ()                 # cap ignored for a hoard role
    assert bias.gather_targets == ("meat",)              # still gathers past the cap
    # A non-hoard role with the same cap still backs off.
    main = economy_bias({"meat": 95}, bottleneck=["meat"], caps={"meat": 100})
    assert main.gather_targets == ()


def test_gather_candidates_target_short_resources_on_march_channels():
    bias = economy_bias({"coal": 5}, bottleneck=["coal"])
    cands = gather_candidates(bias)
    assert len(cands) == 1
    c = cands[0]
    assert c.domain == "gather"
    assert c.channel_kind == MARCH
    assert c.key == "gather_coal"
    # boosted gather outranks an ordinary raid for a march lane
    assert c.priority > domain_priority("raids")


def test_starved_to_gather_end_to_end():
    bias = economy_bias({"coal": 5}, bottleneck=["coal"])
    dec = coordinate([Channel("m1", MARCH)], gather_candidates(bias), {"coal": 5})
    assert [c.action.key for c in dec.commits] == ["gather_coal"]


def _slate_with(*specs_levels):
    picks = tuple(
        BuildCandidate(instance_id=s, spec_id=s, track="economy", to_level="2",
                       to_rank=2.0, value=55.0, cost_total=0, affordable=True, time_s=0)
        for s in specs_levels
    )
    return BuildSlate(picks=picks, candidates=picks, reason="selected")


def test_producer_boost_lifts_the_short_producer_candidate():
    fur = BuildingSpec("coal_mine", "Coal Mine", (LevelReq("1", 1.0, (), (), 0, None),
                                                   LevelReq("2", 2.0, (), (), 0, None)))
    saw = BuildingSpec("sawmill", "Sawmill", (LevelReq("1", 1.0, (), (), 0, None),
                                              LevelReq("2", 2.0, (), (), 0, None)))
    graph = BuildGraph(buildings={"coal_mine": fur, "sawmill": saw})
    slate = _slate_with("coal_mine", "sawmill")
    bias = economy_bias({"coal": 5}, bottleneck=["coal"])
    cands = from_build_slate(slate, graph, instance_boosts=bias.producer_boost)
    by = {c.key: c.priority for c in cands}
    assert by["coal_mine"] > by["sawmill"]               # short producer ranked up
