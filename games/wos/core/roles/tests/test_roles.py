"""Role profiles: resolution, category mapping, multipliers."""
from __future__ import annotations

from games.wos.core.roles import (
    DEFAULT_ROLE_ID,
    ROLES,
    branch_category,
    get_role,
    multiplier,
)


def test_get_role_falls_back_to_default():
    assert get_role("farm").id == "farm"
    assert get_role("FARM").id == "farm"          # case-insensitive
    assert get_role(None).id == DEFAULT_ROLE_ID
    assert get_role("nonsense").id == DEFAULT_ROLE_ID


def test_branch_category_maps_troop_branches_to_battle():
    assert branch_category("growth") == "growth"
    assert branch_category("economy") == "economy"
    assert branch_category("battle") == "battle"
    assert branch_category("t12_infantry") == "battle"


def test_growth_is_universal_in_every_role():
    # No role de-prioritises Growth — it's universal profit (march queue, speeds).
    for role in ROLES.values():
        assert multiplier(role, "growth") == 1.0


def test_farm_favours_economy_fighter_favours_battle():
    # Down-weight model: the favoured category ranks above the other, and neither
    # is lifted above Growth (all multipliers ≤ 1.0).
    farm, fighter = get_role("farm"), get_role("fighter")
    assert multiplier(farm, "economy") > multiplier(farm, "battle")
    assert multiplier(fighter, "battle") > multiplier(fighter, "economy")
    for role in (farm, fighter):
        assert max(role.mult.values()) <= 1.0
