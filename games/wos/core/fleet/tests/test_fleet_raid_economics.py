"""Raid economics: lootable, right-sizing by load, ROI."""
from __future__ import annotations

from games.wos.core.fleet.raid_economics import (
    lootable,
    raid_value,
    size_army_for_load,
    total_load,
)
from games.wos.core.resources.troop_stats import TroopStat

# synthetic stats — A is load-efficient (carry/power high), B is power-dense
A = ("infantry", 1, 0)
B = ("marksman", 5, 0)


def _stat(type_, tier, fc, *, load, power):
    return TroopStat(type=type_, tier=tier, fc=fc, name="x", power=power,
                     attack=1, defense=1, lethality=1, health=1, load=load)


STATS = {
    A: _stat(*A, load=100, power=3),
    B: _stat(*B, load=200, power=10),
}


def test_lootable_scalar_floor():
    assert lootable({"wood": 500, "food": 300}, 100) == {"wood": 400, "food": 200}


def test_lootable_per_resource_floor():
    assert lootable({"wood": 500, "food": 300}, {"wood": 450}) == {"wood": 50, "food": 300}


def test_size_army_prefers_load_efficiency():
    # A carries 100/3≈33 per power, B 200/10=20 per power → prefer A to keep power home
    army = size_army_for_load({A: 10, B: 10}, 250, STATS)
    assert army == {A: 3}                       # ceil(250/100)=3 → 300 load
    assert total_load(army, STATS) == 300


def test_size_army_falls_through_to_next_unit():
    army = size_army_for_load({A: 2, B: 10}, 500, STATS)
    # A capped at 2 (=200 load), remaining 300 → B ceil(300/200)=2 (=400)
    assert army == {A: 2, B: 2}
    assert total_load(army, STATS) >= 500


def test_raid_value_carries_and_weights():
    rv = raid_value({"wood": 500, "food": 300}, {A: 10, B: 10}, stats=STATS)
    assert rv.carried_total == 800           # ample capacity → carry all lootable
    assert rv.plunder == {"wood": 500, "food": 300}
    assert rv.value == 800.0                 # default weights = 1.0
    assert rv.roi == 800.0                   # cost factors default 0
    assert rv.feasible is True


def test_raid_value_capacity_limited():
    rv = raid_value({"wood": 1000}, {A: 3}, stats=STATS)  # cap = 300
    assert rv.carried_total == 300
    assert rv.plunder == {"wood": 300}


def test_raid_value_weights_and_cost():
    rv = raid_value(
        {"wood": 500, "food": 300}, {A: 10, B: 10}, stats=STATS,
        weights={"wood": 2.0, "food": 1.0},
        distance=100.0, march_cost_per_dist=1.0,   # round-trip cost = 200
    )
    assert rv.value == 1300.0                 # 500*2 + 300*1
    assert rv.cost == 200.0
    assert rv.roi == 1100.0


def test_raid_value_empty_farm_not_feasible():
    rv = raid_value({"wood": 0}, {A: 10}, stats=STATS)
    assert rv.feasible is False
    assert rv.carried_total == 0
