"""Tests for the Sunfire Castle territory loader (games/wos/core/sunfire_castle/territory)."""
from __future__ import annotations

from games.wos.core.sunfire_castle.territory import (
    BUFF_TYPES,
    iter_structures,
    iter_towers,
    iter_zones,
    load_territory,
)


def test_structure_counts() -> None:
    t = load_territory()
    assert t.grid_size == 1200
    assert t.castle.kind == "castle"
    assert (t.castle.col, t.castle.row) == (597, 597)
    assert len(t.turrets) == 4
    assert len(t.strongholds) == 4
    assert len(t.fortresses) == 12
    # castle + 4 turrets + 4 strongholds + 12 fortresses
    assert sum(1 for _ in iter_structures(t)) == 21


def test_tower_counts_and_types() -> None:
    t = load_territory()
    assert len(t.towers) == 74
    assert {tw.buff_type for tw in t.towers} == set(BUFF_TYPES)
    # tech is the Research-speed "lab"; all 8 types present
    assert any(tw.buff_type == "tech" and tw.bonus.startswith("Research") for tw in t.towers)


def test_tower_id_unique_and_deterministic() -> None:
    a = load_territory()
    ids = [tw.tower_id for tw in a.towers]
    assert len(ids) == len(set(ids)), "tower_id must be unique"
    # cache returns the same object; ids are stable across the iterator helper
    assert ids == [tw.tower_id for tw in iter_towers()]
    # id format f"{buff_type}_l{level}_{i}"
    sample = a.towers[0]
    assert sample.tower_id == f"{sample.buff_type}_l{sample.level}_0"


def test_booster_pct_parsed() -> None:
    t = load_territory()
    # every tower carries a positive numeric buff parsed from "+N%"
    assert all(tw.booster_pct > 0 for tw in t.towers)
    expedition = next(tw for tw in t.towers if tw.buff_type == "expedition")
    assert expedition.booster_pct == 15.0  # "+15%"


def test_dist_from_castle() -> None:
    t = load_territory()
    # a tower exactly on the castle column/row band is closer than a map-edge one
    nearest = min(t.towers, key=lambda tw: tw.dist_from_castle)
    farthest = max(t.towers, key=lambda tw: tw.dist_from_castle)
    assert nearest.dist_from_castle < farthest.dist_from_castle
    assert nearest.dist_from_castle >= 0.0


def test_zones() -> None:
    t = load_territory()
    zones = list(iter_zones(t))
    assert len(zones) == 3
    ids = {z.id for z in zones}
    assert ids == {"w", "m", "p"}
    # bands are nested: castle-core (w) ⊂ inner (m) ⊂ outer (p)
    by_id = {z.id: z for z in zones}
    assert by_id["w"].min_col > by_id["m"].min_col > by_id["p"].min_col
    assert by_id["w"].max_col < by_id["m"].max_col < by_id["p"].max_col
