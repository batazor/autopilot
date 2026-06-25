"""Dense-cost readiness net for the coordinator.

Today most domain adapters emit empty ``cost`` (the ``item_to_resource`` map is
unfilled — see ``adapters.from_build_slate``), so cross-domain contention on the
shared pool is largely dormant. Once those costs are marked up, several domains
will fight over the same coal/iron across *different* channels. These golden tests
exercise that future state now, so the intended behaviour is pinned before the
markup lands and any later change to :func:`coordinate` is caught.

They also encode the one place the priority-greedy allocator is a *deliberate*
heuristic rather than a one-shot optimum (``test_greedy_is_priority_first_…``).
That test documents the trade-off and is the concrete case to flip against if an
OR/CP-SAT ``strategy="optimal"`` is ever slotted in behind ``coordinate``.
"""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    MARCH,
    RESEARCH,
    CandidateAction,
    Channel,
    Utility,
    coordinate,
)


def _cand(domain, kind, key, priority, cost=None):
    return CandidateAction(domain=domain, channel_kind=kind, key=key,
                           utility=Utility(base_value=priority), cost=cost or {})


def test_shared_resource_couples_domains_across_different_channels():
    # Research (RESEARCH) and building (CONSTRUCTION) each want all the coal, on
    # their own free channels. Priority wins the coal; the loser keeps its channel
    # idle this tick and is reported as the bottleneck for the economy loop.
    channels = [Channel("r1", RESEARCH), Channel("c1", CONSTRUCTION)]
    cands = [
        _cand("research", RESEARCH, "tech", 900, {"coal": 1000}),
        _cand("building_progression", CONSTRUCTION, "furnace", 850, {"coal": 1000}),
    ]
    dec = coordinate(channels, cands, {"coal": 1000})
    assert [c.action.key for c in dec.commits] == ["tech"]   # higher priority runs now
    assert [c.key for c in dec.starved] == ["furnace"]       # CONSTRUCTION idle this tick
    assert dec.bottleneck_resources == ("coal",)
    assert dec.remaining["coal"] == 0


def test_multi_resource_shortfall_reports_every_bottleneck():
    # A candidate short on two resources surfaces both, so the economy loop can
    # lift production of each (not just the first one checked).
    channels = [Channel("c1", CONSTRUCTION)]
    cands = [_cand("building_progression", CONSTRUCTION, "x", 850, {"coal": 100, "iron": 100})]
    dec = coordinate(channels, cands, {"coal": 10, "iron": 10})
    assert dec.commits == ()
    assert dec.bottleneck_resources == ("coal", "iron")


def test_greedy_is_priority_first_not_sum_maximizing():
    # DELIBERATE TRADE-OFF (the online-allocator choice), not a bug:
    #   one high-priority action (900, coal 100) vs two mediums on other channels
    #   (500 each, coal 60 each); coal=120.
    # Greedy honours priority → runs the single 900 now, starves both mediums
    # (committed priority sum = 900). A one-shot optimiser would instead pack the
    # two mediums (sum 1000, fits 120) and defer the 900. For a per-tick allocator
    # with regenerating resources, doing the most important thing first is correct:
    # the 900 is high-priority *because* we want it sooner, and the starved mediums
    # run next tick. If sum-maximisation per tick is ever wanted, swap in a CP-SAT
    # `strategy="optimal"` behind coordinate() — and this assertion flips.
    channels = [Channel("r1", RESEARCH), Channel("c1", CONSTRUCTION), Channel("m1", MARCH)]
    cands = [
        _cand("research", RESEARCH, "top", 900, {"coal": 100}),
        _cand("building_economy", CONSTRUCTION, "mid_a", 500, {"coal": 60}),
        _cand("gather", MARCH, "mid_b", 500, {"coal": 60}),
    ]
    dec = coordinate(channels, cands, {"coal": 120})
    assert [c.action.key for c in dec.commits] == ["top"]
    assert {c.key for c in dec.starved} == {"mid_a", "mid_b"}
    assert dec.remaining["coal"] == 20
