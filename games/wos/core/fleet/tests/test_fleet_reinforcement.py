"""Reinforcement logistics: arrive-in-time + flip-defense sizing."""
from __future__ import annotations

from games.wos.core.fleet.reinforcement_logistics import (
    can_arrive,
    march_time_s,
    plan_reinforcement,
    size_reinforcement,
)
from games.wos.core.resources.troop_stats import TroopStat

B = ("marksman", 5, 0)
STATS = {B: TroopStat(type="marksman", tier=5, fc=0, name="x", power=10,
                      attack=1, defense=1, lethality=1, health=1, load=200)}


def test_march_time_and_arrival():
    assert march_time_s(110, 11) == 10.0
    assert can_arrive(110, 11, attack_eta_s=20) is True
    assert can_arrive(110, 11, attack_eta_s=5) is False


def test_size_reinforcement_reaches_power():
    army = size_reinforcement(900, {B: 1000}, STATS)
    assert army == {B: 90}                       # ceil(900/10)


def test_plan_sufficient_and_in_time():
    plan = plan_reinforcement(
        attacker_power=1000, defender_power=200, available={B: 1000},
        stats=STATS, speed=11, distance=110, attack_eta_s=20, margin=1.1,
    )
    # target = ceil(1100), needed = 900 → 90 marksmen (power 900); 200+900>=1100
    assert plan.needed_power == 900
    assert plan.power == 900
    assert plan.sufficient is True
    assert plan.arrives_in_time is True
    assert plan.worth_sending is True


def test_plan_too_slow_not_worth():
    plan = plan_reinforcement(
        attacker_power=1000, defender_power=200, available={B: 1000},
        stats=STATS, speed=11, distance=110, attack_eta_s=5,   # arrives in 10s > 5s
    )
    assert plan.arrives_in_time is False
    assert plan.worth_sending is False


def test_plan_insufficient_troops():
    plan = plan_reinforcement(
        attacker_power=1000, defender_power=200, available={B: 10},  # only 100 power
        stats=STATS, speed=11, distance=0, attack_eta_s=999,
    )
    assert plan.sufficient is False
    assert plan.worth_sending is False


def test_plan_already_defended_needs_nothing():
    plan = plan_reinforcement(
        attacker_power=500, defender_power=600, available={B: 1000},
        stats=STATS, distance=0, attack_eta_s=999,
    )
    assert plan.needed_power == 0
    assert plan.sufficient is True
    # nothing to send → not "worth sending" (power 0)
    assert plan.worth_sending is False
