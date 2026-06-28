"""Build-queue ETA tracker: occupancy counting + the diagnose summary."""
from __future__ import annotations

from games.wos.core.building.common.exec import _busy_build_queues

from agentctl.core import _build_queue_summary
from config.building_name_parser import parse_building_name_level_instance
from config.buildings import get_building_registry

_NOW = 1_000_000.0


def _state() -> dict[str, str]:
    return {
        "buildings.upgrading.coal_mine": str(int(_NOW + 1800)),  # 30m left
        "buildings.upgrading.furnace": str(int(_NOW - 100)),  # already finished
        "buildings.upgrading.shelter": str(int(_NOW + 600)),  # 10m left
        "buildings.upgrading.shelter.to_level": "3",  # a side field, not a slot
        "buildings.levels.coal_mine": "2",
        "planner.build_total_queues": "2",
    }


def test_busy_build_queues_counts_only_future_finishes() -> None:
    # coal_mine + shelter are still upgrading; furnace finished; .to_level ignored.
    assert _busy_build_queues(_state(), _NOW) == 2
    # Past the shelter finish but before coal_mine → one slot frees.
    assert _busy_build_queues(_state(), _NOW + 700) == 1
    # After both → all free.
    assert _busy_build_queues(_state(), _NOW + 2000) == 0
    assert _busy_build_queues({}, _NOW) == 0


def test_build_queue_summary_shape_and_order() -> None:
    summary = _build_queue_summary(_state(), _NOW)
    assert summary["total_queues"] == 2
    assert summary["busy_queues"] == 2
    assert summary["free_queues"] == 0
    # Sorted soonest-first: shelter (10m) before coal_mine (30m).
    buildings = [u["building"] for u in summary["upgrading"]]
    assert buildings == ["shelter", "coal_mine"]
    assert summary["upgrading"][0]["eta"] == "10m00s"
    assert summary["upgrading"][1]["eta"] == "30m00s"


def test_build_queue_summary_empty_is_all_free() -> None:
    summary = _build_queue_summary({}, _NOW)
    assert summary == {
        "total_queues": 2,
        "busy_queues": 0,
        "free_queues": 2,
        "upgrading": [],
    }


def test_ru_indexed_plate_resolves_to_canonical_building_id() -> None:
    # «Барак 2» carries a plate index that parse surfaces as instance_id
    # "shelter_2"; the level must be keyed by the canonical id «shelter» so the
    # planner (which reads buildings.levels.<id>) actually sees it. Regression
    # for the phantom buildings.levels.shelter_2 written by sync_building_name.
    buildings = get_building_registry().buildings
    parsed = parse_building_name_level_instance("Барак 2 Ур. 2", buildings)
    assert parsed is not None
    building, level, instance_id = parsed
    assert building.id == "shelter"
    assert level == 2
    assert instance_id == "shelter_2"  # the discriminator we deliberately drop
