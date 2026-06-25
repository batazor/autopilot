"""Tests for the buff-tower capture planner (games/wos/core/sunfire_castle/tower_plan)."""
from __future__ import annotations

from games.wos.core.roles import get_role
from games.wos.core.sunfire_castle.territory import load_territory
from games.wos.core.sunfire_castle.tower_plan import (
    BUFF_CATEGORY,
    rank_towers,
    tower_value,
)


def _first_value(ranking: object, buff_type: str) -> float:
    """Highest value among a ranking's picks for one buff type."""
    return max(c.value for c in ranking.picks if c.buff_type == buff_type)


def test_ranks_all_uncontrolled_by_default() -> None:
    r = rank_towers(target_count=0)  # 0 = all
    assert r.total_towers == 74
    assert r.controlled == 0
    assert r.available == 74
    assert len(r.picks) == 74
    # sorted by value descending
    vals = [c.value for c in r.picks]
    assert vals == sorted(vals, reverse=True)


def test_controlled_towers_excluded() -> None:
    all_ids = [tw.tower_id for tw in load_territory().towers]
    held = {all_ids[0]: True, all_ids[1]: True, all_ids[2]: False}  # 2 truly held
    r = rank_towers(controlled=held, target_count=0)
    assert r.controlled == 2
    assert r.available == 72
    picked_ids = {c.tower_id for c in r.picks}
    assert all_ids[0] not in picked_ids
    assert all_ids[1] not in picked_ids
    assert all_ids[2] in picked_ids  # held=False → still available


def test_fighter_prefers_combat_over_economy() -> None:
    # weapon (BATTLE) and gathering (ECONOMY) both have +5% base towers, so role is
    # the only differentiator. fighter: ECONOMY halved → combat out-ranks economy.
    r = rank_towers(role="fighter", target_count=0)
    assert _first_value(r, "weapon") > _first_value(r, "gathering")
    assert _first_value(r, "defense") > _first_value(r, "production")


def test_farm_prefers_economy_over_combat() -> None:
    r = rank_towers(role="farm", target_count=0)
    assert _first_value(r, "gathering") > _first_value(r, "weapon")
    assert _first_value(r, "production") > _first_value(r, "defense")


def test_role_changes_battle_tower_value() -> None:
    t = load_territory()
    weapon = next(tw for tw in t.towers if tw.buff_type == "weapon")
    max_dist = max(tw.dist_from_castle for tw in t.towers)
    v_fighter, m_fighter = tower_value(weapon, get_role("fighter"), max_dist=max_dist)
    v_farm, m_farm = tower_value(weapon, get_role("farm"), max_dist=max_dist)
    assert m_fighter == 1.0
    assert m_farm == 0.5
    assert v_fighter > v_farm


def test_category_map_covers_all_buff_types() -> None:
    types = {tw.buff_type for tw in load_territory().towers}
    assert types <= set(BUFF_CATEGORY)


def test_target_count_limits_picks() -> None:
    r = rank_towers(role="balanced", target_count=5)
    assert len(r.picks) == 5
    assert sum(r.by_type.values()) == 5


def test_deterministic() -> None:
    a = rank_towers(role="fighter", target_count=10)
    b = rank_towers(role="fighter", target_count=10)
    assert [c.tower_id for c in a.picks] == [c.tower_id for c in b.picks]
