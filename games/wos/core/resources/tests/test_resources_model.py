"""Pure affordability math: can_afford across every resource kind."""
from __future__ import annotations

from games.wos.core.resources.model import (
    INSUFFICIENT_STAMINA,
    NO_FREE_HERO,
    NO_FREE_SLOT,
    NO_TROOPS,
    UNOBSERVED_BLOCKED,
    Action,
    ActionTable,
    WorldView,
    can_afford,
    round_trip_seconds,
)


def _table(troops_observed: bool = True, heroes_observed: bool = True) -> ActionTable:
    return ActionTable.from_dict({
        "resources": {
            "march_slots": {"kind": "slot_lease", "observed": True},
            "stamina": {"kind": "pool_regen", "observed": True, "cap": 200, "regen_per_hour": 12},
            "troops": {"kind": "typed_pool", "observed": troops_observed,
                       "types": ["infantry", "lancer", "marksman"]},
            "heroes": {"kind": "exclusive_set", "observed": heroes_observed,
                       "roles": ["combat", "gatherer"]},
        },
    })


def _world(slots_free: int = 3, stamina: float | None = 100.0, **kw) -> WorldView:
    return WorldView(
        slots_capacity=kw.get("slots_capacity", 6),
        slots_free=slots_free,
        stamina_est=stamina,
        troops_free=kw.get("troops", {"infantry": 100_000, "lancer": 100_000, "marksman": 100_000}),
        troops_observed=kw.get("troops_observed", True),
        free_heroes=kw.get("heroes", {
            "combat": ("jessie", "natalia", "flint", "gina"),
            "gatherer": ("cloris", "eugene", "charlie", "smith"),
        }),
        heroes_observed=kw.get("heroes_observed", True),
    )


def _beast(**over) -> Action:
    base = {
        "id": "beast_hunt", "task_type": "beast_hunt", "priority": 40,
        "costs": [
            {"resource": "march_slots", "amount": 1},
            {"resource": "stamina", "amount": 10},
            {"resource": "troops", "type": "any", "amount": 60_000},
            {"resource": "heroes", "role": "combat", "count": 3},
        ],
    }
    base.update(over)
    return Action.from_dict(base)


def test_all_resources_available_affords_and_assigns():
    aff = can_afford(_beast(), _world(), _table(), unobserved_policy="block")
    assert aff.ok is True
    assert aff.assignment is not None
    assert aff.assignment.heroes == ("jessie", "natalia", "flint")   # first 3 free combat
    assert aff.assignment.troops == {"any": 60_000}


def test_no_free_slot_blocks():
    aff = can_afford(_beast(), _world(slots_free=0), _table(), unobserved_policy="block")
    assert aff.ok is False
    assert [b.reason for b in aff.blocks] == [NO_FREE_SLOT]


def test_insufficient_stamina_blocks():
    aff = can_afford(_beast(), _world(stamina=5), _table(), unobserved_policy="block")
    assert any(b.reason == INSUFFICIENT_STAMINA for b in aff.blocks)


def test_unread_stamina_blocks():
    aff = can_afford(_beast(), _world(stamina=None), _table(), unobserved_policy="block")
    assert any(b.reason == INSUFFICIENT_STAMINA for b in aff.blocks)


def test_no_troops_blocks():
    world = _world(troops={"infantry": 1000, "lancer": 0, "marksman": 0})
    aff = can_afford(_beast(), world, _table(), unobserved_policy="block")
    assert any(b.reason == NO_TROOPS for b in aff.blocks)


def test_not_enough_free_heroes_blocks():
    world = _world(heroes={"combat": ("jessie", "natalia"), "gatherer": ()})
    aff = can_afford(_beast(), world, _table(), unobserved_policy="block")
    assert any(b.reason == NO_FREE_HERO for b in aff.blocks)


def test_collects_all_blocking_resources():
    world = _world(slots_free=0, stamina=0)
    aff = can_afford(_beast(), world, _table(), unobserved_policy="block")
    reasons = {b.reason for b in aff.blocks}
    assert NO_FREE_SLOT in reasons and INSUFFICIENT_STAMINA in reasons


def test_unobserved_blocks_under_block_policy():
    aff = can_afford(_beast(), _world(), _table(troops_observed=False), unobserved_policy="block")
    assert any(b.reason == UNOBSERVED_BLOCKED and b.resource == "troops" for b in aff.blocks)


def test_unobserved_assumed_available_under_optimistic_policy():
    table = _table(troops_observed=False, heroes_observed=False)
    aff = can_afford(_beast(), _world(), table, unobserved_policy="optimistic")
    assert aff.ok is True
    assert aff.assignment is not None
    assert aff.assignment.heroes == ()          # nothing concrete to assign yet
    assert aff.assignment.troops == {}


def test_round_trip_seconds_counts_travel_both_ways():
    # 10m out + 5m participation + 10m back (1:1 return) = 25m.
    assert round_trip_seconds(600, 300) == 1500
    assert round_trip_seconds(600, 300, return_ratio=0.5) == 1200   # faster return
    assert round_trip_seconds(0, 0) == 0


def test_shipped_table_carries_lease_durations():
    table = ActionTable.load()
    leases = {a.id: a.lease_seconds for a in table.actions}
    assert leases["gather_resources"] == 21600        # hours
    assert leases["rally_join"] > 0                    # round-trip placeholder
    assert leases["beast_hunt"] < leases["gather_resources"]


def test_multiple_hero_lines_do_not_double_book():
    action = Action.from_dict({
        "id": "mixed", "task_type": "mixed",
        "costs": [
            {"resource": "heroes", "role": "combat", "count": 2},
            {"resource": "heroes", "role": "any", "count": 2},
        ],
    })
    world = _world(heroes={"combat": ("a", "b"), "gatherer": ("c", "d")})
    aff = can_afford(action, world, _table(), unobserved_policy="block")
    assert aff.ok is True
    assert len(set(aff.assignment.heroes)) == 4   # a,b consumed by combat; c,d by any
