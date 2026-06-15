from __future__ import annotations

from typing import TYPE_CHECKING, get_type_hints

import pytest

import config.module_discovery as module_discovery
import config.paths as paths
import navigation.screen_graph as screen_graph
from config.reload import reload_config
from navigation import template_icon_resolver  # noqa: F401
from navigation.screen_graph import route_hops_async, route_taps

if TYPE_CHECKING:
    from pathlib import Path


def test_route_taps_type_hints_are_resolvable() -> None:
    hints = get_type_hints(route_taps)

    assert "return" in hints


def test_building_routes_back_to_main_city() -> None:
    assert route_taps("building", "main_city") == [["from.building.to.main_city"]]


def _write_reload_edge_module(tmp_path: Path, edge_taps: str) -> None:
    mod = tmp_path / "games" / "wos" / "core" / "nav_reload"
    (mod / "routes").mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        "id: nav_reload\ntitle: nav reload\nwiki: false\n",
        encoding="utf-8",
    )
    (mod / "routes" / "edge_taps.yaml").write_text(edge_taps, encoding="utf-8")


def test_reload_config_invalidates_screen_graph_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)
    module_discovery._clear_module_discovery_caches()
    screen_graph.invalidate_screen_verify_config()
    _write_reload_edge_module(
        tmp_path,
        "edges:\n  alpha:\n    beta: [tap.alpha.beta]\n",
    )

    try:
        assert route_taps("alpha", "beta") == [["tap.alpha.beta"]]
        _write_reload_edge_module(
            tmp_path,
            "edges:\n  alpha:\n    beta: [tap.alpha.beta]\n    gamma: [tap.alpha.gamma]\n",
        )
        assert route_taps("alpha", "gamma") is None

        reload_config()

        assert route_taps("alpha", "gamma") == [["tap.alpha.gamma"]]
    finally:
        screen_graph.invalidate_screen_verify_config()
        module_discovery._clear_module_discovery_caches()


def test_survivor_status_routes_main_city() -> None:
    assert route_taps("main_city", "survivor_status") == [["isWorkers"]]
    assert route_taps("main_city", "survivor_status.status") == [
        ["isWorkers"],
        ["survivor_status.status"],
    ]
    assert route_taps("survivor_status", "main_city") == [
        ["from.survivor_status.to.main_city"]
    ]


def test_exploration_routes_squad_settings() -> None:
    assert route_taps("exploration", "squad_settings") == [["exploration.to.squad_settings"]]
    assert route_taps("squad_settings", "exploration") == [["icon.page.back"]]


def test_main_world_routes_to_city_with_world_scoped_button() -> None:
    assert route_taps("main_world", "main_city", game="wos") == [
        ["main_world.to.main_city"]
    ]


def test_exploration_defeat_routes_main_city() -> None:
    assert route_taps("exploration.defeat", "main_city") == [["button.to_main_city"]]


def test_welcome_back_routes_to_main_city() -> None:
    assert route_taps("welcome_back", "main_city") == [["button.confirm.green"]]


def test_kingshot_welcome_back_routes_to_main_city() -> None:
    assert route_taps("welcome_back", "main_city", game="kingshot") == [
        ["button.confirm.green"]
    ]


def test_kingshot_main_city_routes() -> None:
    expected = {
        "conquest": [["main_city.to.conquest"]],
        "heroes": [["main_city.to.heroes"]],
        "backpack": [["main_city.to.backpack"]],
        "shop": [["main_city.to.shop"]],
        "main_world": [["main_city.to.world"]],
        "mail": [["mail.new"]],
        "governor_profile": [["to_governor_profile"]],
        "chief_profile": [["to_governor_profile"]],
        "vip": [["page.vip"]],
    }
    for target, taps in expected.items():
        assert route_taps("main_city", target, game="kingshot") == taps


def test_kingshot_mail_tab_routes() -> None:
    assert route_taps("main_city", "mail.wars", game="kingshot") == [
        ["mail.new"],
        ["mail.tab.wars"],
    ]
    assert route_taps("mail", "mail.alliance", game="kingshot") == [
        ["mail.tab.alliance"]
    ]
    assert route_taps("mail.system", "mail.reports", game="kingshot") == [
        ["mail.tab.reports"]
    ]
    assert route_taps("mail.starred", "main_city", game="kingshot") == [
        ["button.back"]
    ]
    assert route_taps("mail.system", "mail.letter", game="kingshot") == [
        ["mail.gift"]
    ]
    assert route_taps("mail.letter", "mail.system", game="kingshot") == [
        ["mail.letter.back", "mail.tab.system"]
    ]
    assert route_taps("mail.system", "mail.delete_confirm", game="kingshot") == [
        ["mail.delete.all"]
    ]
    assert route_taps("mail.delete_confirm", "mail", game="kingshot") == [
        ["mail.delete.confirm"]
    ]


@pytest.mark.asyncio
async def test_kingshot_event_routes_from_main_city_by_template_icon() -> None:
    hops = await route_hops_async(
        "main_city",
        "event.fishing_tournament",
        instance_id="bs1",
        redis_client=None,
        game="kingshot",
    )

    assert hops == [
        (
            "event.fishing_tournament",
            [
                {
                    "type": "template_icon",
                    "region": "main_city.icon_search",
                    "template": (
                        "games/kingshot/events/fishing_tournament/references/"
                        "main_city.to.fishing_tournament.png"
                    ),
                    "threshold": 0.9,
                }
            ],
        )
    ]


def test_ads_natalia_routes_to_main_city() -> None:
    assert route_taps("ads.natalia", "main_city") == [["ads.natalia.title"]]


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


def test_trials_day_routes() -> None:
    assert route_taps("event.trials", "event.trials.day.1") == [["trial.day.1"]]
    assert route_taps("event.trials.day.1", "event.trials.day.3") == [["trial.day.3"]]
    assert route_taps("event.trials.day.5", "main_city") == [["icon.page.back"]]


@pytest.mark.asyncio
async def test_trials_routes_from_main_city_by_template_icon() -> None:
    hops = await route_hops_async(
        "main_city",
        "event.trials",
        instance_id="bs1",
        redis_client=None,
    )

    assert hops == [
        (
            "event.trials",
            [
                {
                    "type": "template_icon",
                    "region": "main_city.icon_search",
                    "template": "games/wos/events/trials/references/event.trials.png",
                    "threshold": 0.9,
                }
            ],
        )
    ]


@pytest.mark.asyncio
async def test_deals_routes_from_main_world_by_template_icon() -> None:
    hops = await route_hops_async(
        "main_world",
        "deals",
        instance_id="bs1",
        redis_client=None,
        game="wos",
    )

    assert hops == [
        (
            "deals",
            [
                {
                    "type": "template_icon",
                    "region": "main_city.icon_search",
                    "template": (
                        "games/wos/deals/deals/references/crop/"
                        "main_city_main_city.to.deals.png"
                    ),
                    "threshold": 0.85,
                }
            ],
        )
    ]


@pytest.mark.asyncio
async def test_7_day_routes_from_main_city_by_template_icon() -> None:
    hops = await route_hops_async(
        "main_city",
        "event.7-day",
        instance_id="bs1",
        redis_client=None,
    )

    assert hops == [
        (
            "event.7-day",
            [
                {
                    "type": "template_icon",
                    "region": "main_city.icon_search",
                    "template": "games/wos/events/7-day/references/main_city.event.7-day.png",
                    "threshold": 0.9,
                }
            ],
        )
    ]


def test_romance_season_routes_from_main_city_by_labeled_icon() -> None:
    assert route_taps("main_city", "event.romance_season", game="wos") == [
        ["main_city.to.romance_season"]
    ]
    assert route_taps("event.romance_season", "main_city", game="wos") == [
        ["icon.page.back"]
    ]


def test_frostdragon_tyrant_routes_to_main_city() -> None:
    assert route_taps("text.frostdragon_tyrant", "main_city") == [
        ["text.tap_any_blank_space_to_close"]
    ]


def test_rewards_routes_to_main_city() -> None:
    assert route_taps("rewards", "main_city") == [
        ["button.click_to_continue"]
    ]


def test_rewards_upgraded_routes_to_deals() -> None:
    assert route_taps("rewards.upgraded", "deals") == [
        ["rewards.upgraded.tap_anywhere_to_exit"]
    ]


def test_rewards_upgraded_routes_to_main_city_via_deals() -> None:
    assert route_taps("rewards.upgraded", "main_city") == [
        ["rewards.upgraded.tap_anywhere_to_exit"],
        ["icon.page.back"],
    ]


def test_claimed_routes_to_shop_daily_deals() -> None:
    assert route_taps("claimed", "shop.daily_deals") == [
        ["claimed.tap_anywhere_to_exit"]
    ]


def test_claimed_routes_to_main_city_via_shop_daily_deals() -> None:
    assert route_taps("claimed", "main_city") == [
        ["claimed.tap_anywhere_to_exit"],
        ["icon.page.back"],
    ]


def test_increase_level_routes_back_to_vip() -> None:
    assert route_taps("increase_level", "vip") == [["increase_level.icon.close"]]
    assert route_taps("increase_level", "vip", game="kingshot") == [
        ["increase_level.icon.close"]
    ]


def test_heroes_sr_new_routes_to_main_city() -> None:
    assert route_taps("heroes.sr.new", "main_city") == [["heroes.sr.new.close"]]
