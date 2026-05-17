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


def test_exploration_defeat_routes_main_city() -> None:
    assert route_taps("exploration.defeat", "main_city") == [["button.to_main_city"]]


def test_welcome_back_routes_to_main_city() -> None:
    assert route_taps("welcome_back", "main_city") == [["button.confirm.green"]]


def test_ads_natalia_routes_to_main_city() -> None:
    assert route_taps("ads.natalia", "main_city") == [["ads.natalia"]]


def test_is_new_people_routes_to_and_from_main_city() -> None:
    assert route_taps("main_city", "isNewPeople") == [["isNewPeople"]]
    assert route_taps("isNewPeople", "main_city") == [["button.welcome_in"]]


def test_mail_routes_to_and_from_main_city() -> None:
    assert route_taps("main_city", "mail") == [["mail.new"]]
    assert route_taps("mail", "main_city") == [["icon.page.back"]]


def test_mail_tab_routes() -> None:
    assert route_taps("main_city", "mail.wars") == [["mail.new"], ["mail.tab.wars"]]
    assert route_taps("mail", "mail.alliance") == [["mail.tab.alliance"]]
    assert route_taps("mail.system", "mail.reports") == [["mail.tab.reports"]]
    assert route_taps("mail.starred", "main_city") == [["icon.page.back"]]


def test_frostdragon_tyrant_routes_to_main_city() -> None:
    assert route_taps("text.frostdragon_tyrant", "main_city") == [
        ["text.tap_any_blank_space_to_close"]
    ]


def test_rewards_routes_to_main_city() -> None:
    assert route_taps("rewards", "main_city") == [["button.tap_anywhere_to_exit"]]


def test_increase_level_routes_back_to_vip() -> None:
    assert route_taps("increase_level", "vip") == [["increase_level.icon.close"]]


def test_heroes_sr_new_routes_to_main_city() -> None:
    assert route_taps("heroes.sr.new", "main_city") == [["heroes.sr.new.close"]]
