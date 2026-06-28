"""Unit tests for the idle Chapter-objective router — pure parse/classify/gate
over the REAL building graph + registry and the REAL ``chapter_objectives.yaml``,
so the planner gate and the regexes are validated against realistic text."""
from __future__ import annotations

from games.wos.core.building.planner import load_graph
from games.wos.core.chapter.exec import (
    _load_objectives,
    _match_building_in_text,
    _route_chapter_objective,
)

from config.buildings import get_building_registry

_BUILDINGS = get_building_registry().buildings
_GRAPH = load_graph()
_OBJECTIVES = _load_objectives()


def _route(text: str, levels: dict) -> dict:
    return _route_chapter_objective(
        text, levels=levels, graph=_GRAPH, buildings=_BUILDINGS, objectives=_OBJECTIVES
    )


def test_objectives_registry_loads() -> None:
    assert _OBJECTIVES, "chapter_objectives.yaml registry failed to load"


def test_building_name_matched_inside_objective_text() -> None:
    # The building name is wrapped in verbs/levels, so it's a substring match.
    coal = _match_building_in_text("Upgrade Coal Mine to Lv. 5", _BUILDINGS)
    assert coal is not None and coal.id == "coal_mine"
    # RU «Белая мгла» localisation ("Угольная шахта" → Coal Mine).
    ru = _match_building_in_text("Улучшить Угольная шахта", _BUILDINGS)
    assert ru is not None and ru.id == "coal_mine"
    furnace = _match_building_in_text("Upgrade Furnace", _BUILDINGS)
    assert furnace is not None and furnace.id == "furnace"


def test_ru_declined_objective_names_match_via_lemmas() -> None:
    # RU objectives decline the building name to the accusative ("улучшите
    # Кухню", not the nominative «Кухня»); a plain substring of the registry
    # alias misses (я→ю). pymorphy3 lemmatisation maps every case back to the
    # lemma, so the declined plates still resolve. Regression for the live
    # «Кухню: улучшите до ур. 3» objective that previously matched nothing.
    cases = {
        "Кухню: улучшите до ур. 3 (2/3)": "cookhouse",
        "Лесопилку: улучшите до ур. 5": "sawmill",
        "Угольный рудник: улучшите до ур. 3": "coal_mine",
        "Барак 2: улучшите до ур. 3": "shelter",
    }
    for text, expected in cases.items():
        b = _match_building_in_text(text, _BUILDINGS)
        assert b is not None and b.id == expected, (text, None if b is None else b.id)


def test_building_feasible_routes_to_building_upgrade() -> None:
    # Coal Mine unbuilt, furnace high enough that the planner's ready step IS the
    # objective building → feasible, hand off to building.upgrade.
    d = _route("Upgrade Coal Mine to Lv. 1", {"furnace": 10})
    assert d["kind"] == "building"
    assert d["building"] == "coal_mine"
    assert d["scenario"] == "building.upgrade"
    assert d["feasible"] is True


def test_building_blind_falls_back_to_in_game_button() -> None:
    # No anchor level read yet → planner can't judge prereqs; defer to the
    # in-game Upgrade/Build button rather than skip.
    d = _route("Upgrade Coal Mine", {})
    assert d["kind"] == "building"
    assert d["scenario"] == "building.upgrade"
    assert d["feasible"] is True
    assert d["reason"] == "planner_blind_fallback"


def test_building_prereq_pending_skips() -> None:
    # Furnace known but below Coal Mine's gate → the planner advances the furnace
    # prerequisite, not coal_mine → skip (tapping chapter.task would no-op).
    d = _route("Upgrade Coal Mine", {"furnace": 1})
    assert d["kind"] == "building"
    assert d["building"] == "coal_mine"
    assert d["scenario"] is None
    assert d["feasible"] is False
    assert d["reason"] == "prereq_pending"


def test_shelter_ru_and_en_route_to_building_upgrade() -> None:
    # bs5 case: the Chapter objective is the Shelter ("Барак"). Shelter is
    # multi-instance but the graph models a base "shelter" spec, so it gates to a
    # push in both RU and EN, blind or with a furnace anchor.
    for text in ("Улучшить Барак", "Upgrade Shelter"):
        for levels in ({}, {"furnace": 10}):
            d = _route(text, levels)
            assert d["kind"] == "building", (text, levels)
            assert d["building"] == "shelter", (text, levels)
            assert d["scenario"] == "building.upgrade", (text, levels)
            assert d["feasible"] is True, (text, levels)


def test_live_ru_coal_mine_objective_routes_to_building_upgrade() -> None:
    # Real bs4 OCR of the Chapter tracker — the «Белая мгла» build names Coal Mine
    # "Угольный рудник" (not the earlier "Угольная шахта" guess), with the usual
    # degraded glyphs. Furnace 3 (Coal Mine's gate) is read → planner's ready step
    # is the coal mine itself → push building.upgrade.
    d = _route("Угольный рудник: улучшите дор! 3[(1/3)", {"furnace": 3})
    assert d["kind"] == "building"
    assert d["building"] == "coal_mine"
    assert d["scenario"] == "building.upgrade"
    assert d["feasible"] is True


def test_non_building_research_routes_to_idle_research() -> None:
    d = _route("Research a technology", {"furnace": 10})
    assert d["kind"] == "scenario"
    assert d["scenario"] == "start_idle_research"
    assert d["feasible"] is True


def test_non_building_recognised_but_unwired_is_logged_not_pushed() -> None:
    d = _route("Increase your Power", {"furnace": 10})
    assert d["kind"] == "scenario"
    assert d["scenario"] is None
    assert d["reason"] == "unautomated"


def test_empty_objective_is_noop() -> None:
    assert _route("", {})["reason"] == "empty"


def test_unrecognised_objective_is_noop() -> None:
    d = _route("zzz qqq", {"furnace": 10})
    assert d["kind"] == "none"
    assert d["scenario"] is None
    assert d["reason"] == "unrecognised"
