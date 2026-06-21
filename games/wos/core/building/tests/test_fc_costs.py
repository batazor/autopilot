"""Fire-Crystal cost tables: exact per-sublevel costs + cumulative-cost sum."""
from __future__ import annotations

import pytest
from games.wos.core.building.fc_costs import (
    cumulative_cost,
    levels_for,
    load_fc_costs,
)


def test_loads_exact_furnace_costs():
    table = load_fc_costs()
    furnace = table["furnace"]
    assert len(furnace) == 46                       # FC 1 .. FC 10 with sublevels
    fc1 = furnace[0]
    assert fc1.id == "fc1_0" and fc1.tier == "FC 1"
    assert fc1.cost["meat"] == 67_000_000           # exact integers (vs our approx item-icon)
    assert fc1.cost["coal"] == 13_000_000
    assert fc1.cost["fire_crystal"] == 132


def test_camps_share_one_table():
    inf = levels_for("infantry_camp")
    assert inf and inf == levels_for("lancer_camp") == levels_for("marksman_camp")
    assert inf == levels_for("camp")


def test_cumulative_cost_sums_intermediate_levels():
    # fc1_0 → fc1_4 sums the four sublevels fc1_1..fc1_4 (each 72M meat).
    total = cumulative_cost("furnace", "fc1_0", "fc1_4")
    assert total["meat"] == 4 * 72_000_000
    # from the bottom up to the first level = just that level.
    assert cumulative_cost("furnace", None, "fc1_0")["meat"] == 67_000_000


def test_unknown_level_raises_and_unknown_building_is_empty():
    assert levels_for("nonexistent") == ()
    with pytest.raises(KeyError):
        cumulative_cost("furnace", None, "fc99_9")
