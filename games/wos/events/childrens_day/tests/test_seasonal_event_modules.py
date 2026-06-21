"""Seasonal event module wiring for the Children's Day/Romance batch."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from navigation import main_menu_panel_resolver, screen_graph  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[5]
EVENTS_DIR = REPO_ROOT / "games" / "wos" / "events"

MODULES = {
    "childrens_day": {
        "title": "Children's Day",
        "node": "event.childrens_day",
        "section": "childrens_day",
        "rows": ["childrens_day"],
        "conds": ["main_menu.panel.childrens_day.childrens_day.isClaimable == true"],
    },
    "popularity_king_competition": {
        "title": "Popularity King Competition",
        "node": "event.popularity_king_competition",
        "section": "popularity_king",
        "rows": [
            "popularity_king_competition",
            "polar_popularity",
            "sweet_heart_castle",
            "heart_belongs_castle",
        ],
        "conds": [
            "main_menu.panel.popularity_king.popularity_king_competition.isClaimable == true",
            "main_menu.panel.popularity_king.polar_popularity.isClaimable == true",
            "main_menu.panel.popularity_king.sweet_heart_castle.isClaimable == true",
            "main_menu.panel.popularity_king.heart_belongs_castle.isClaimable == true",
        ],
    },
    "rose_defense_battle": {
        "title": "Rose Defense Battle",
        "node": "event.rose_defense_battle",
        "section": "rose_defense",
        "rows": ["rose_defense_battle", "bloom_battle", "flower_eating_beasts"],
        "conds": [
            "main_menu.panel.rose_defense.rose_defense_battle.isClaimable == true",
            "main_menu.panel.rose_defense.bloom_battle.isClaimable == true",
            "main_menu.panel.rose_defense.flower_eating_beasts.isClaimable == true",
        ],
    },
    "honey_language_mall": {
        "title": "Honey Language Mall",
        "node": "event.honey_language_mall",
        "section": "honey_language_mall",
        "rows": ["honey_language_mall", "sweet_whispers_shop"],
        "conds": [
            "main_menu.panel.honey_language_mall.honey_language_mall.isClaimable == true",
            "main_menu.panel.honey_language_mall.sweet_whispers_shop.isClaimable == true",
        ],
    },
    "honeymoon_trip": {
        "title": "Honeymoon Trip",
        "node": "event.honeymoon_trip",
        "section": "honeymoon_trip",
        "rows": ["honeymoon_trip"],
        "conds": ["main_menu.panel.honeymoon_trip.honeymoon_trip.isClaimable == true"],
    },
}


def _load_yaml(module_id: str, rel: str) -> dict:
    path = EVENTS_DIR / module_id / rel
    assert path.exists(), f"missing: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@pytest.mark.parametrize(("module_id", "cfg"), MODULES.items())
def test_seasonal_manifest_declares_working_module(module_id: str, cfg: dict) -> None:
    manifest = _load_yaml(module_id, "module.yaml")

    assert manifest["id"] == module_id
    assert manifest["title"] == cfg["title"]
    assert manifest["enabled"] is True
    assert manifest["scenarios"] == "scenarios"
    assert manifest["analyze"] == "analyze/analyze.yaml"
    assert manifest["references"] == "references"


@pytest.mark.parametrize(("module_id", "cfg"), MODULES.items())
def test_seasonal_analyzers_push_claimable_main_menu_rows(
    module_id: str,
    cfg: dict,
) -> None:
    analyze = _load_yaml(module_id, "analyze/analyze.yaml")
    rules = analyze["overlay"]

    assert [rule["cond"] for rule in rules] == cfg["conds"]
    for rule in rules:
        assert rule["region"] == "main_menu.city"
        assert rule["screens"] == ["main_menu"]
        assert rule["steps"] == [
            {"push_scenario": {"name": cfg["node"], "ttl": "1m"}}
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("module_id", "cfg"), MODULES.items())
async def test_seasonal_routes_use_main_menu_panel_rows(
    module_id: str,
    cfg: dict,
) -> None:
    _ = module_id
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            "main_menu",
            cfg["node"],
            instance_id="bs1",
            redis_client=None,
            game="wos",
        )
        assert hops == [
            (
                cfg["node"],
                [
                    {
                        "type": "main_menu_panel_row",
                        "section": cfg["section"],
                        "rows": cfg["rows"],
                        "approval_region": f"main_menu.panel.{cfg['section']}",
                    }
                ],
            )
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


@pytest.mark.parametrize(("module_id", "cfg"), MODULES.items())
def test_seasonal_scenarios_use_generic_claim_loop(module_id: str, cfg: dict) -> None:
    scenario_name = cfg["node"].split(".", 1)[1]
    scenario = _load_yaml(module_id, f"scenarios/event.{scenario_name}.yaml")

    assert scenario["enabled"] is True
    assert scenario["node"] == cfg["node"]
    assert scenario["cond"] == "active_player != null"
    assert [step["while_match"] for step in scenario["steps"]] == [
        "button.claim",
        "button.claim.big",
    ]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Children's Day", ("childrens_day", "childrens_day")),
        ("Popularity King Competition", ("popularity_king", "popularity_king_competition")),
        ("Polar Popularity", ("popularity_king", "polar_popularity")),
        ("Sweet Heart Castle", ("popularity_king", "sweet_heart_castle")),
        ("Heart Belongs Castle", ("popularity_king", "heart_belongs_castle")),
        ("Rose Defense Battle", ("rose_defense", "rose_defense_battle")),
        ("Bloom Battle", ("rose_defense", "bloom_battle")),
        ("Flower-Eating Beasts", ("rose_defense", "flower_eating_beasts")),
        ("Honey Language Mall", ("honey_language_mall", "honey_language_mall")),
        ("Sweet Whispers Shop", ("honey_language_mall", "sweet_whispers_shop")),
        ("Honeymoon Trip", ("honeymoon_trip", "honeymoon_trip")),
    ],
)
def test_main_menu_scanner_recognizes_seasonal_titles(
    title: str,
    expected: tuple[str, str],
) -> None:
    main_menu_exec = importlib.import_module("games.wos.core.main_menu.exec")

    assert main_menu_exec._section_for_row(title, "", "") == expected
