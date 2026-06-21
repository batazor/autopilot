"""Unit coverage for the pure ranking formula (``scheduler.ranking.compute_rank``).

These pin the arithmetic that ``RedisQueue._rank_candidates`` delegates to —
no Redis, no screen graph (``hops`` is passed in directly).
"""

from __future__ import annotations

from scheduler.ranking import (
    HOPS_DEBUFF_CAP_HOPS,
    HOPS_SENTINEL,
    UNREACHABLE_DEBUFF,
    W_HOPS,
    W_RECENT,
    compute_rank,
)

CAP = 3


def _rank(**over):
    # Default candidate sits OFF its required node (a != b) so recent_debuff is
    # active unless a test opts into the on-node case.
    base = {
        "base_priority": 1000,
        "current_screen": "a",
        "required_node": "b",
        "hops": 0,
        "recent_count": 0,
        "recent_runs_cap": CAP,
        "no_recent_debuff_member": False,
        "run_at": 100.0,
        "created_at": 50.0,
    }
    base.update(over)
    return compute_rank(**base)


def test_no_node_branch_zeroes_graph_terms():
    # No required_node (or no current_screen) => no hops/unreachable penalty.
    key, meta = _rank(required_node="", hops=None)
    assert meta["graph_debuff"] == 0
    assert meta["hops"] == 0
    assert meta["unreachable_flag"] == 0
    assert meta["effective_priority"] == 1000
    assert key == (-1000, 0, 0, 100.0, 50.0)


def test_hops_debuff_scales_and_caps():
    _, two = _rank(current_screen="a", required_node="b", hops=2)
    assert two["graph_debuff"] == W_HOPS * 2
    assert two["hops"] == 2
    # Beyond the cap the debuff plateaus.
    _, far = _rank(current_screen="a", required_node="b", hops=HOPS_DEBUFF_CAP_HOPS + 4)
    assert far["graph_debuff"] == W_HOPS * HOPS_DEBUFF_CAP_HOPS


def test_unreachable_uses_sentinel_and_flat_debuff():
    key, meta = _rank(current_screen="a", required_node="b", hops=None)
    assert meta["unreachable_flag"] == 1
    assert meta["hops"] == HOPS_SENTINEL
    assert meta["graph_debuff"] == UNREACHABLE_DEBUFF
    assert key[1] == 1  # unreachable_flag tiebreaker after priority


def test_recent_debuff_scales_and_caps():
    _, once = _rank(recent_count=1)
    assert once["recent_debuff"] == W_RECENT
    _, many = _rank(recent_count=99)
    assert many["recent_debuff"] == CAP * W_RECENT  # capped at recent_runs_cap


def test_on_required_node_disables_recent_debuff():
    _, meta = _rank(
        current_screen="forge", required_node="forge", hops=0, recent_count=5
    )
    assert meta["on_required_node"] is True
    assert meta["recent_debuff_disabled"] is True
    assert meta["recent_debuff"] == 0


def test_no_recent_debuff_member_disables_recent_debuff():
    _, meta = _rank(recent_count=5, no_recent_debuff_member=True)
    assert meta["recent_debuff_disabled"] is True
    assert meta["recent_debuff"] == 0


def test_effective_priority_subtracts_both_debuffs():
    _, meta = _rank(
        base_priority=10_000,
        current_screen="a",
        required_node="b",
        hops=2,
        recent_count=2,
    )
    assert meta["effective_priority"] == 10_000 - (W_HOPS * 2) - (2 * W_RECENT)
