"""Lookahead projection: forward-simulate construction + research into an ETA timeline."""
from __future__ import annotations

from itertools import pairwise

from games.wos.core.building.planner import load_graph
from games.wos.core.coordinator import project_cycle
from games.wos.core.research.planner import load_research_graph

BG = load_graph()
RG = load_research_graph()


def _max_overlap(tasks) -> int:
    events: list[tuple[int, int]] = []
    for t in tasks:
        events.append((t.start_s, 1))
        events.append((t.end_s, -1))
    events.sort(key=lambda e: (e[0], e[1]))   # a finish (-1) sorts before a start (+1) at the same t
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def test_reaches_the_build_goal_from_scratch():
    proj = project_cycle(
        build_graph=BG, build_levels={}, research_graph=RG,
        construction_queues=2, goal_id="furnace", goal_cap=5.0,
    )
    assert proj.reason == "goal_reached"
    assert proj.timeline
    assert proj.total_time_s > 0
    furnace = [m for m in proj.milestones if m.kind == "build_goal"]
    assert furnace and furnace[-1].label.endswith("5")          # built up to the cap
    ats = [m.at_s for m in proj.milestones]
    assert ats == sorted(ats)                                    # milestones in time order


def test_respects_the_construction_queue_count():
    proj = project_cycle(
        build_graph=BG, build_levels={}, research_graph=RG,
        construction_queues=2, goal_cap=6.0,
    )
    builds = [t for t in proj.timeline if t.channel == "construction"]
    assert _max_overlap(builds) <= 2                             # never more than 2 in parallel


def test_research_waits_for_the_research_center():
    """The cross-domain gate: no tech can start before the RC building exists."""
    proj = project_cycle(
        build_graph=BG, build_levels={}, research_graph=RG,
        construction_queues=2, goal_cap=10.0,
    )
    research = [t for t in proj.timeline if t.channel == "research"]
    rc_builds = [t for t in proj.timeline if t.key == "research_center"]
    if research:                                                 # RC reached a tech gate in-window
        assert rc_builds, "research ran but the RC was never built"
        assert min(t.start_s for t in research) >= min(t.end_s for t in rc_builds)


def test_horizon_caps_the_projection():
    proj = project_cycle(
        build_graph=BG, build_levels={}, research_graph=RG,
        construction_queues=2, goal_cap=30.0, horizon_s=3600,
    )
    assert proj.reason == "horizon"
    assert all(t.start_s <= 3600 for t in proj.timeline)         # nothing started past the horizon


def test_same_queue_tasks_never_overlap():
    proj = project_cycle(
        build_graph=BG, build_levels={}, research_graph=RG,
        construction_queues=3, goal_cap=6.0,
    )
    by_queue: dict[int, list] = {}
    for t in (x for x in proj.timeline if x.channel == "construction"):
        by_queue.setdefault(t.queue, []).append(t)
    for tasks in by_queue.values():
        tasks.sort(key=lambda t: t.start_s)
        for a, b in pairwise(tasks):
            assert b.start_s >= a.end_s


def test_starting_partway_skips_done_levels():
    """From a furnace already at the cap, the goal is immediately reached."""
    proj = project_cycle(
        build_graph=BG, build_levels={"furnace": "5"}, research_graph=RG,
        construction_queues=2, goal_id="furnace", goal_cap=5.0,
    )
    assert proj.reason == "goal_reached"
    assert not any(t.key == "furnace" for t in proj.timeline)    # nothing left to do on furnace
