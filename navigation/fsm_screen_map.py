"""Directed edges between game screens as modeled in the Go FSM.

Mirrors transition tables in (same commit family as main):

- https://github.com/batazor/whiteout-survival-autopilot/blob/main/internal/fsm/transition_event_tundra_adventure.go
- https://github.com/batazor/whiteout-survival-autopilot/blob/main/internal/fsm/transition_main_menu.go
- https://github.com/batazor/whiteout-survival-autopilot/blob/main/internal/fsm/transition_troops.go

State ids match ``internal/domain/state/fsm.go`` string constants.
This module only records topology (from → to); tap/swipe actions live in Go.

The Python :mod:`navigation.navigator` graph covers a small OCR-driven subset
with pixel taps; use this map for parity with the full autopilot FSM.
"""

from __future__ import annotations

from typing import Final


def _freeze_adjacency(raw: dict[str, set[str]]) -> dict[str, frozenset[str]]:
    return {src: frozenset(dst) for src, dst in raw.items()}


# transition_event_tundra_adventure.go — tundraAdventureTransitionPaths
_TUNDRA_RAW: Final[dict[str, set[str]]] = {
    "tundra_adventure": {
        "tundra_adventure_main",
        "tundra_adventure_drill",
        "tundra_adventure_odessey",
        "tundra_adventure_caravan",
        "main_city",
    },
    "tundra_adventure_main": {
        "tundra_adventure_drill",
        "tundra_adventure_odessey",
        "tundra_adventure_caravan",
        "main_city",
    },
    "tundra_adventure_drill": {
        "tundra_adventurer_drill",
        "tundra_adventurer_daily_missions",
        "tundra_adventure_main",
        "main_city",
    },
    "tundra_adventurer_drill": {
        "main_city",
        "tundra_adventurer_daily_missions",
        "tundra_adventure_main",
        "tundra_adventure_odessey",
        "tundra_adventure_caravan",
    },
    "tundra_adventurer_daily_missions": {
        "main_city",
        "tundra_adventurer_drill",
        "tundra_adventure_main",
        "tundra_adventure_odessey",
        "tundra_adventure_caravan",
    },
    "tundra_adventure_odessey": {"tundra_adventure_main", "main_city"},
    "tundra_adventure_caravan": {"tundra_adventure_main", "main_city"},
}

# transition_main_menu.go — mainMenuTransitionPaths
_MAIN_MENU_RAW: Final[dict[str, set[str]]] = {
    "main_menu_city": {
        "main_city",
        "main_menu_wilderness",
        "main_menu_building_1",
        "main_menu_building_2",
        "infantry_city_view",
        "lancer_city_view",
        "marksman_city_view",
        "main_menu_tech_research",
        "vip",
        "exploration",
        "alliance_manage",
        "chief_profile",
    },
}

# transition_troops.go — troopsTransitionPaths (empty step lists still imply reachability)
_TROOPS_RAW: Final[dict[str, set[str]]] = {
    "infantry_city_view": {"main_city", "main_menu_city", "marksman_city_view"},
    "lancer_city_view": {"main_city", "main_menu_city"},
    "marksman_city_view": {"main_city", "main_menu_city", "arena_city_view", "fishing_main"},
}

# Hub screen ↔ survivor/worker list (Python tap graph; see navigation/edge_taps.yaml).
_MAIN_CITY_HUB_RAW: Final[dict[str, set[str]]] = {
    "main_city": {"survivor_status", "suggestion_box"},
    "survivor_status": {"main_city"},
    "suggestion_box": {"main_city"},
}

TUNDRA_ADVENTURE_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_TUNDRA_RAW)
MAIN_MENU_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_MAIN_MENU_RAW)
TROOPS_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_TROOPS_RAW)
MAIN_CITY_HUB_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_MAIN_CITY_HUB_RAW)


def merge_fsm_edges(
    *parts: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    """Union destinations for the same source across partial graphs."""
    merged: dict[str, set[str]] = {}
    for part in parts:
        for src, dsts in part.items():
            merged.setdefault(src, set()).update(dsts)
    return {s: frozenset(d) for s, d in merged.items()}


FSM_SCREEN_EDGES: Final[dict[str, frozenset[str]]] = merge_fsm_edges(
    TUNDRA_ADVENTURE_EDGES,
    MAIN_MENU_EDGES,
    TROOPS_EDGES,
    MAIN_CITY_HUB_EDGES,
)
