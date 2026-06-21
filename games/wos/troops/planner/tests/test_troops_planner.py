"""Troop-training planner — army-composition ranking (pure)."""

from games.wos.troops.planner import plan_training, rank_troops


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
