from __future__ import annotations

from typing import get_type_hints

from navigation.screen_graph import route_taps


def test_route_taps_type_hints_are_resolvable() -> None:
    hints = get_type_hints(route_taps)

    assert "return" in hints


def test_building_routes_back_to_main_city() -> None:
    assert route_taps("building", "main_city") == [["from.building.to.main_city"]]


def test_survivor_status_routes_main_city() -> None:
    assert route_taps("main_city", "survivor_status") == [["isWorkers"]]
    assert route_taps("survivor_status", "main_city") == [
        ["from.survivor_status.to.main_city"]
    ]


def test_exploration_routes_squad_settings() -> None:
    assert route_taps("exploration", "squad_settings") == [["exploration.to.squad_settings"]]
    assert route_taps("squad_settings", "exploration") == [["icon.page.back"]]


def test_welcome_back_routes_to_main_city() -> None:
    assert route_taps("welcome_back", "main_city") == [["button.confirm.green"]]
