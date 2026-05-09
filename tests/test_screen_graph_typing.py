from __future__ import annotations

from typing import get_type_hints

from navigation.screen_graph import route_taps


def test_route_taps_type_hints_are_resolvable() -> None:
    hints = get_type_hints(route_taps)

    assert "return" in hints


def test_building_routes_back_to_main_city() -> None:
    assert route_taps("building", "main_city") == [["from.building.to.main_city"]]
