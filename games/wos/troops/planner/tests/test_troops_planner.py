"""Troop-training planner — army-composition ranking + value-greedy (type, tier) pick."""

from types import SimpleNamespace

from games.wos.troops.planner import (
    NONE,
    PROMOTE,
    SELECTED,
    TRAIN,
    TrainTier,
    plan_next,
    plan_training,
    rank_troops,
)

# Synthetic training cost/time table (tier → cost + per-troop time).
COSTS = {
    4: TrainTier(4, {"meat": 800, "coal": 30}, 40),
    5: TrainTier(5, {"meat": 1000, "coal": 50}, 60),
}


def _stat(name, power):
    return SimpleNamespace(name=name, power=power)


# Synthetic stats so the assertions don't depend on exact game numbers.
STATS = {
    ("infantry", 3, 0): _stat("Senior", 30),
    ("infantry", 5, 0): _stat("Warrior", 50),
    ("lancer", 5, 0): _stat("Lancer5", 55),
    ("marksman", 5, 0): _stat("Marksman5", 60),
}


def test_no_counts_uses_meta_target_order():
    # Default target: infantry .34 > lancer .33 = marksman .33 → infantry first,
    # ties to TROOP_TYPES order (lancer before marksman).
    assert rank_troops() == ("infantry", "lancer", "marksman")


def test_counts_rank_by_largest_deficit():
    # marksman is far below its target share → train it first; infantry is over.
    counts = {"infantry": 9000, "lancer": 1000, "marksman": 0}
    assert rank_troops(counts)[0] == "marksman"


def test_balanced_army_falls_back_to_target_order():
    counts = {"infantry": 100, "lancer": 100, "marksman": 100}
    # Equal shares → deficit ranking follows the target weights (infantry leads).
    assert rank_troops(counts)[0] == "infantry"


def test_plan_training_picks_best_idle():
    counts = {"infantry": 9000, "lancer": 1000, "marksman": 0}
    # marksman is most-wanted but busy → next-most-wanted idle camp wins.
    assert plan_training({"infantry", "lancer"}, counts) == "lancer"
    assert plan_training({"infantry", "lancer", "marksman"}, counts) == "marksman"


def test_plan_training_none_when_no_idle():
    assert plan_training(set()) is None


def test_custom_target():
    # Marksman-heavy target → marksman ranks first with no counts.
    assert rank_troops(target={"infantry": 0.2, "lancer": 0.2, "marksman": 0.6})[0] == "marksman"


# --- plan_next: value-greedy (type, tier) ------------------------------------
def test_plan_next_trains_most_deficient_type_at_capped_tier():
    counts = {"infantry": 9000, "lancer": 1000, "marksman": 0}   # marksman most deficient
    plan = plan_next(counts, max_tier=5, stats=STATS)
    assert plan.reason == SELECTED
    assert plan.step.troop_type == "marksman"
    assert plan.step.tier == 5                                   # highest unlocked ≤ cap
    assert plan.step.power == 60                                 # per-unit power from stats
    assert plan.step.deficit > 0                                 # it's under its target share


def test_plan_next_per_type_tier_cap():
    # No counts → meta order (infantry first); infantry's camp caps at tier 3.
    plan = plan_next(max_tier={"infantry": 3, "lancer": 5, "marksman": 5}, stats=STATS)
    assert plan.step.troop_type == "infantry"
    assert plan.step.tier == 3
    assert plan.step.power == 30


def test_plan_next_skips_unbuilt_camp():
    # Infantry camp not built (cap 0) → skipped; next meta type (lancer) wins.
    plan = plan_next(max_tier={"infantry": 0, "lancer": 5, "marksman": 5}, stats=STATS)
    assert plan.step.troop_type == "lancer"


def test_plan_next_none_when_nothing_trainable():
    plan = plan_next(max_tier=0, stats=STATS)
    assert plan.reason == NONE
    assert plan.step is None


def test_plan_next_real_stats_default_to_top_tier():
    plan = plan_next()                                          # real troop-stats, cap 11
    assert plan.step.tier == 11
    assert plan.step.power > 0
    assert plan.step.troop_type == "infantry"                  # meta order, no counts


# --- cost / time + promotion (the training-calculator model) -----------------
def test_plan_next_fills_cost_and_time_from_table():
    plan = plan_next(max_tier=5, batch=10, stats=STATS, costs=COSTS)
    assert plan.step.kind == TRAIN
    assert plan.step.cost == {"meat": 10000, "coal": 500}      # T5 cost × batch 10
    assert plan.step.time_s == 600


def test_plan_next_appends_cheaper_promote_candidate():
    plan = plan_next(max_tier=5, batch=10, stats=STATS, costs=COSTS)
    promote = [c for c in plan.candidates if c.kind == PROMOTE]
    assert promote
    p = promote[0]
    assert p.cost == {"meat": 2000, "coal": 200}               # diff T5−T4 × 10
    assert p.time_s == (60 - 40) * 10
    assert sum(p.cost.values()) < sum(plan.step.cost.values())  # cheaper than fresh


def test_plan_next_no_cost_data_keeps_empty():
    plan = plan_next(max_tier=5, stats=STATS, costs={})        # empty table
    assert plan.step.cost == {}
    assert plan.step.time_s == 0
    assert all(c.kind != PROMOTE for c in plan.candidates)     # no promote without data
