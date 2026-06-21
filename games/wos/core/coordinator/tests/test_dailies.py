"""Daily-task bias: boost open tasks (harder near reset), nudge one-shots, claim."""
from __future__ import annotations

from games.wos.core.coordinator import (
    DailyTask,
    daily_bias,
    domain_priority,
    merge_boosts,
)
from games.wos.core.coordinator.dailies import DEFAULT_BOOST, DEFAULT_URGENT_MULT

HOUR = 3600


def test_open_task_boosts_its_domains():
    bias = daily_bias([DailyTask("t_train", "train", target=1, progress=0)])
    assert bias.domain_boost["troops"] == DEFAULT_BOOST
    assert bias.domain_boost["building_camp"] == DEFAULT_BOOST


def test_done_task_no_boost():
    bias = daily_bias([DailyTask("t_train", "train", target=1, progress=1)])
    assert bias.domain_boost == {}


def test_claimable_task_collected():
    bias = daily_bias([DailyTask("t_done", "build", target=1, progress=1, claimable=True)])
    assert bias.claims == ("t_done",)


def test_urgency_amplifies_near_reset():
    task = [DailyTask("t_res", "research", target=2, progress=0)]
    calm = daily_bias(task, seconds_to_reset=12 * HOUR)
    rush = daily_bias(task, seconds_to_reset=1 * HOUR)
    assert rush.domain_boost["research"] > calm.domain_boost["research"]
    assert rush.domain_boost["research"] == DEFAULT_BOOST * DEFAULT_URGENT_MULT


def test_one_shot_category_is_nudged():
    bias = daily_bias([DailyTask("t_recruit", "recruit", target=1, progress=0)])
    assert [n.category for n in bias.nudges] == ["recruit"]


def test_non_one_shot_not_nudged():
    bias = daily_bias([DailyTask("t_build", "build", target=1, progress=0)])
    assert bias.nudges == ()


def test_merge_boosts_takes_max():
    merged = merge_boosts({"research": 1.5, "gather": 1.2}, {"research": 1.3, "troops": 1.6})
    assert merged == {"research": 1.5, "gather": 1.2, "troops": 1.6}


def test_daily_boost_lifts_priority_via_merge():
    bias = daily_bias([DailyTask("t_res", "research", progress=0)])
    boosts = merge_boosts(bias.domain_boost)
    lifted = domain_priority("research", boost=boosts.get("research", 1.0))
    assert lifted == domain_priority("research") * DEFAULT_BOOST
