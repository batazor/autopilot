"""Maximum-weight assignment (pure Hungarian) — verified against brute force."""
from __future__ import annotations

import itertools

from games.wos.core.fleet.matchmaking import assign_max_weight, plan_raids


def _brute_max_square(value):
    n = len(value)
    return max(
        sum(value[i][perm[i]] for i in range(n))
        for perm in itertools.permutations(range(n))
    )


def _total(value, pairs):
    return sum(value[r][c] for r, c in pairs)


def test_two_by_two_diagonal():
    value = [[3, 1], [1, 3]]
    assert sorted(assign_max_weight(value)) == [(0, 0), (1, 1)]


def test_greedy_would_fail():
    # greedy by max picks (0,0)=10 then (1,1)=1 = 11; optimal swaps for 9+9=18
    value = [[10, 9], [9, 1]]
    pairs = assign_max_weight(value)
    assert _total(value, pairs) == 18
    assert sorted(pairs) == [(0, 1), (1, 0)]


def test_matches_brute_force_deterministic():
    n = 5
    value = [[(i * 7 + j * 13 + i * j * 3) % 17 for j in range(n)] for i in range(n)]
    pairs = assign_max_weight(value)
    assert len(pairs) == n
    assert {r for r, _ in pairs} == set(range(n))   # every row matched once
    assert len({c for _, c in pairs}) == n          # every col distinct
    assert _total(value, pairs) == _brute_max_square(value)


def test_rectangular_more_farms_than_fighters():
    # 2 fighters x 3 farms; pick the best 2 disjoint
    value = [[5, 1, 1], [1, 1, 6]]
    pairs = sorted(assign_max_weight(value))
    assert pairs == [(0, 0), (1, 2)]


def test_rectangular_more_fighters_than_farms():
    value = [[5, 1], [1, 6], [3, 3]]   # 3 fighters x 2 farms
    pairs = assign_max_weight(value)
    assert len(pairs) == 2                          # only 2 farms to take
    assert _total(value, pairs) == 11               # (0,0)=5 + (1,1)=6


def test_plan_raids_returns_ids_and_filters_min_value():
    fighters = ["G1", "G2"]
    farms = ["F1", "F2"]
    value = [[800.0, 50.0], [50.0, 30.0]]
    plan = plan_raids(fighters, farms, value, min_value=40.0)
    # optimal: G1→F1 (800), G2→F2 (30) — but 30 < min_value → dropped
    assert ("G1", "F1", 800.0) in plan
    assert all(v > 40.0 for _, _, v in plan)


def test_empty():
    assert assign_max_weight([]) == []
    assert plan_raids([], [], []) == []
