"""Unrolling the furnace-first plan into a time-ordered build schedule."""
from __future__ import annotations

from itertools import pairwise

from games.wos.core.building.planner import (
    GOAL_REACHED,
    SELECTED,
    BuildGraph,
    BuildingSpec,
    LevelReq,
    apply_speed,
    load_graph,
    project_multi_schedule,
    project_schedule,
)


def _lvl(level, rank, prereqs=(), time_s=0):
    return LevelReq(level=level, rank=rank, prereqs=prereqs, cost=(), time_s=time_s, power=None)


def _graph(*specs):
    return BuildGraph(buildings={s.id: s for s in specs})


# Furnace 1→3 with Embassy gating each step; durations chosen so cumulative
# timing is easy to assert.
FURNACE = BuildingSpec("furnace", "Furnace", (
    _lvl("1", 1.0, time_s=10),
    _lvl("2", 2.0, (("embassy", 1.0),), time_s=100),
    _lvl("3", 3.0, (("embassy", 2.0),), time_s=1000),
))
EMBASSY = BuildingSpec("embassy", "Embassy", (
    _lvl("1", 1.0, (("furnace", 1.0),), time_s=5),
    _lvl("2", 2.0, (("furnace", 2.0),), time_s=50),
    _lvl("3", 3.0, (("furnace", 3.0),), time_s=500),
))
GRAPH = _graph(FURNACE, EMBASSY)


def test_unrolls_full_furnace_first_order():
    sched = project_schedule(GRAPH, {}, goal_cap=3.0)
    # furnace-first, recursing into the lagging Embassy prereq at each gate.
    order = [(s.building_id, s.to_level) for s in sched.steps]
    assert order == [
        ("furnace", "1"),
        ("embassy", "1"),
        ("furnace", "2"),
        ("embassy", "2"),
        ("furnace", "3"),
    ]
    assert sched.reason == GOAL_REACHED
    assert sched.truncated is False


def test_cumulative_timing_is_end_to_end():
    sched = project_schedule(GRAPH, {}, goal_cap=3.0)
    # Bars are laid back-to-back: each start == previous end.
    for prev, cur in zip(sched.steps, sched.steps[1:], strict=False):
        assert cur.start_s == prev.end_s
    assert sched.steps[0].start_s == 0
    # 10 + 5 + 100 + 50 + 1000
    assert sched.total_time_s == 1165
    assert sched.steps[-1].end_s == sched.total_time_s


def test_seq_and_from_levels_are_tracked():
    sched = project_schedule(GRAPH, {}, goal_cap=3.0)
    assert [s.seq for s in sched.steps] == [1, 2, 3, 4, 5]
    furnace_steps = [s for s in sched.steps if s.building_id == "furnace"]
    assert [(s.from_level, s.to_level) for s in furnace_steps] == [
        ("0", "1"), ("1", "2"), ("2", "3"),
    ]


def test_starts_from_existing_levels():
    sched = project_schedule(GRAPH, {"furnace": 2, "embassy": 2}, goal_cap=3.0)
    assert [(s.building_id, s.to_level) for s in sched.steps] == [("furnace", "3")]
    assert sched.steps[0].from_level == "2"
    assert sched.total_time_s == 1000


def test_goal_already_reached_is_empty():
    sched = project_schedule(GRAPH, {"furnace": 3, "embassy": 3}, goal_cap=3.0)
    assert sched.steps == ()
    assert sched.total_time_s == 0
    assert sched.reason == GOAL_REACHED


def test_max_steps_truncates_and_flags():
    sched = project_schedule(GRAPH, {}, goal_cap=3.0, max_steps=2)
    assert len(sched.steps) == 2
    assert sched.truncated is True
    assert sched.reason == SELECTED


def test_real_graph_reaches_furnace_30():
    graph = load_graph()
    sched = project_schedule(graph, {})
    assert sched.reason == GOAL_REACHED
    assert sched.truncated is False
    # The final furnace upgrade reaches the cap.
    furnace_steps = [s for s in sched.steps if s.building_id == "furnace"]
    assert furnace_steps[-1].to_rank == 30.0
    # A from-scratch build-out is hundreds of levels and a positive total time.
    assert len(sched.steps) > 100
    assert sched.total_time_s > 0
    # Every prerequisite is built before the step that needs it (timeline is valid).
    assert all(s.start_s <= s.end_s for s in sched.steps)


# --- multi-queue parallel projection -----------------------------------------

# Furnace↔Embassy critical chain + an independent economy producer (Sawmill is
# in policy.PRODUCERS, so plan_builds offers it to idle queues).
M_FURNACE = BuildingSpec("furnace", "Furnace", (
    _lvl("1", 1.0, time_s=10),
    _lvl("2", 2.0, (("embassy", 1.0),), time_s=100),
    _lvl("3", 3.0, (("embassy", 2.0),), time_s=100),
))
M_EMBASSY = BuildingSpec("embassy", "Embassy", (
    _lvl("1", 1.0, (("furnace", 1.0),), time_s=50),
    _lvl("2", 2.0, (("furnace", 2.0),), time_s=50),
))
M_SAWMILL = BuildingSpec("sawmill", "Sawmill", (
    _lvl("1", 1.0, time_s=30),
    _lvl("2", 2.0, time_s=30),
    _lvl("3", 3.0, time_s=30),
    _lvl("4", 4.0, time_s=30),
))
M_GRAPH = _graph(M_FURNACE, M_EMBASSY, M_SAWMILL)


def _overlaps(a, b):
    return a.start_s < b.end_s and b.start_s < a.end_s


def test_multi_runs_economy_in_parallel():
    sched = project_multi_schedule(M_GRAPH, {}, queues=2, goal_cap=3.0)
    assert sched.reason == GOAL_REACHED
    assert sched.queues == 2
    furnace = [s for s in sched.steps if s.building_id == "furnace"]
    assert furnace[-1].to_level == "3"                      # goal reached
    # The 2nd queue filled idle time with economy (Sawmill), tagged + on queue 1.
    economy = [s for s in sched.steps if s.track == "economy"]
    assert economy and any(s.building_id == "sawmill" for s in economy)
    assert {s.queue for s in sched.steps} == {0, 1}
    # Genuine parallelism: some furnace-chain step overlaps an economy step.
    chain = [s for s in sched.steps if s.track == "progression"]
    assert any(_overlaps(c, e) for c in chain for e in economy)


def test_multi_never_double_books_a_plot():
    sched = project_multi_schedule(M_GRAPH, {}, queues=2, goal_cap=3.0)
    by_plot: dict[str, list] = {}
    for s in sched.steps:
        by_plot.setdefault(s.instance_id, []).append(s)
    for runs in by_plot.values():
        runs.sort(key=lambda s: s.start_s)
        for a, b in pairwise(runs):
            assert a.end_s <= b.start_s                     # same plot never overlaps itself


def test_multi_parallelism_never_slower_than_single_queue():
    # With more queues the critical path can only get shorter (or equal).
    one = project_multi_schedule(M_GRAPH, {}, queues=1, goal_cap=3.0)
    two = project_multi_schedule(M_GRAPH, {}, queues=2, goal_cap=3.0)
    assert two.total_time_s <= one.total_time_s


def test_single_queue_multi_matches_furnace_first():
    # queues=1 ⇒ progression always outranks economy and never idles, so the
    # sim collapses to the pure furnace-first chain (no economy gets built).
    multi1 = project_multi_schedule(GRAPH, {}, queues=1, goal_cap=3.0)
    flat = project_schedule(GRAPH, {}, goal_cap=3.0)
    assert multi1.total_time_s == flat.total_time_s
    assert all(s.track == "progression" for s in multi1.steps)


def test_real_graph_two_queues_beats_one_and_reaches_goal():
    graph = load_graph()
    one = project_schedule(graph, {})
    two = project_multi_schedule(graph, {}, queues=2)
    assert two.reason == GOAL_REACHED
    assert two.truncated is False
    assert two.queues == 2
    # Furnace still tops out at 30, and parallel queues finish no later than one.
    furnace = [s for s in two.steps if s.building_id == "furnace"]
    assert furnace[-1].to_rank == 30.0
    assert two.total_time_s <= one.total_time_s
    assert all(s.start_s <= s.end_s for s in two.steps)
    # Both queues are used and the idle 2nd queue picked up economy/camp work.
    assert {s.queue for s in two.steps} == {0, 1}
    assert any(s.track in {"economy", "camp"} for s in two.steps)


# --- construction-speed buff (hero builder shortens durations) ----------------
def test_apply_speed_formula():
    assert apply_speed(100, 0) == 100          # no buff → raw time
    assert apply_speed(100, 100) == 50         # +100% → half the time
    assert apply_speed(0, 50) == 0
    assert apply_speed(100, -5) == 100         # nonsense buff ignored


def test_construction_speed_buff_shortens_the_schedule():
    base = project_schedule(GRAPH, {}, goal_cap=3.0)
    fast = project_schedule(GRAPH, {}, goal_cap=3.0, construction_speed_pct=100.0)
    assert fast.construction_speed_pct == 100.0
    assert fast.total_time_s < base.total_time_s
    assert fast.steps[0].duration_s == apply_speed(base.steps[0].duration_s, 100.0)


def test_multi_schedule_speed_buff_pulls_in_the_furnace_eta():
    base = project_multi_schedule(GRAPH, {}, queues=2, goal_cap=3.0)
    fast = project_multi_schedule(GRAPH, {}, queues=2, goal_cap=3.0, construction_speed_pct=50.0)
    assert fast.total_time_s < base.total_time_s
