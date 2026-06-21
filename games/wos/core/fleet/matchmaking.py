"""Optimal fighter↔farm assignment — maximum-weight bipartite matching.

Pure, dependency-free (scipy isn't in the runtime): a self-contained **Hungarian
algorithm** (Kuhn–Munkres, O(n²m) potentials form) solves the assignment so the
fleet raids the highest *total* ROI, not greedy first-fit. ``plan_raids`` takes a
fighter×farm value matrix (from ``raid_economics.raid_value``) and returns the
optimal one-to-one assignment, dropping pairs below ``min_value``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _hungarian_min_cost(cost: Sequence[Sequence[float]]) -> list[int]:
    """Min-cost assignment for an ``n × m`` matrix with ``n <= m``.

    Returns ``row_to_col`` (length ``n``): the column assigned to each row.
    Standard potentials/augmenting-path Hungarian (e-maxx form), 1-indexed
    internally.
    """
    n = len(cost)
    m = len(cost[0]) if n else 0
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)      # p[j] = row (1-indexed) matched to column j; 0 = none
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    row_to_col = [-1] * n
    for j in range(1, m + 1):
        if p[j] != 0:
            row_to_col[p[j] - 1] = j - 1
    return row_to_col


def assign_max_weight(value: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """One-to-one ``(row, col)`` assignment maximising total value (rectangular ok)."""
    n = len(value)
    m = len(value[0]) if n else 0
    if n == 0 or m == 0:
        return []
    transpose = n > m   # the Hungarian form needs rows <= cols
    mat = (
        [[value[i][j] for i in range(n)] for j in range(m)]
        if transpose
        else [list(row) for row in value]
    )
    hi = max(max(row) for row in mat)
    cost = [[hi - x for x in row] for row in mat]   # maximise value ⇒ minimise (hi − value)
    row_to_col = _hungarian_min_cost(cost)
    pairs: list[tuple[int, int]] = []
    for i, j in enumerate(row_to_col):
        if j < 0:
            continue
        pairs.append((j, i) if transpose else (i, j))
    return pairs


def plan_raids(
    fighter_ids: Sequence[str],
    farm_ids: Sequence[str],
    value_matrix: Sequence[Sequence[float]],
    *,
    min_value: float = 0.0,
) -> list[tuple[str, str, float]]:
    """Optimal ``(fighter, farm, value)`` assignments (rows=fighters, cols=farms),
    dropping any pair whose value isn't strictly above ``min_value``."""
    out: list[tuple[str, str, float]] = []
    for r, c in assign_max_weight(value_matrix):
        v = float(value_matrix[r][c])
        if v > min_value:
            out.append((fighter_ids[r], farm_ids[c], v))
    return out
