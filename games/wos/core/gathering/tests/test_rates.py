"""Gather-rate formula: time scales inversely with boost; yield/hour is the ROI."""
from __future__ import annotations

from games.wos.core.gathering.rates import (
    BASE_GATHER_TIME_S,
    NODE_MAX_LV8,
    gather_time_s,
    gathered_amount,
    total_boost_pct,
    yield_per_hour,
)


def test_time_scales_inversely_with_boost():
    assert gather_time_s(0) == BASE_GATHER_TIME_S
    assert gather_time_s(100) == BASE_GATHER_TIME_S / 2     # +100% → half the time
    assert gather_time_s(200) == BASE_GATHER_TIME_S / 3


def test_total_boost_combines_sources():
    assert total_boost_pct(expedition_level=3) == 15
    assert total_boost_pct(city_bonus=True) == 100
    assert total_boost_pct(node_pct=25, expedition_level=5, city_bonus=True) == 150


def test_gathered_amount_caps_and_scales():
    node = NODE_MAX_LV8["meat"]
    full = gather_time_s(0)
    assert gathered_amount(node, full, 0) == node            # enough time → full node
    assert gathered_amount(node, full * 2, 0) == node        # capped, never exceeds
    assert gathered_amount(node, full / 2, 0) == node // 2   # linear before the cap
    assert gathered_amount(node, 0, 0) == 0


def test_yield_per_hour_doubles_with_full_boost():
    base = yield_per_hour(NODE_MAX_LV8["coal"], 0)
    boosted = yield_per_hour(NODE_MAX_LV8["coal"], 100)
    assert boosted == base * 2                               # half time → double rate
    assert base > 0
