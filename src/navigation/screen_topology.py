"""Full game screen topology (node graph): directed edges between screen ids.

Mirrors Go transition tables (historical ``internal/fsm/`` sources). Records
topology only (from → to); tap sequences live in :mod:`navigation.screen_graph`
(``edge_taps.yaml``).

The OCR-driven tap graph in :mod:`navigation.navigator` is a smaller subset used
at runtime; this map is the merged reference topology for the Routes UI.
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
        "chat",
        "alliance_manage",
        "chief_profile",
    },
}

# exploration ↔ main menu (Go FSM parity; tap routing still lives in edge_taps when labeled)
_EXPLORATION_RAW: Final[dict[str, set[str]]] = {
    "exploration": {
        "main_menu_city",
    },
}

_CHAT_RAW: Final[dict[str, set[str]]] = {
    "chat": {
        "main_menu_city",
    },
}

# transition_troops.go — troopsTransitionPaths (empty step lists still imply reachability)
_TROOPS_RAW: Final[dict[str, set[str]]] = {
    "infantry_city_view": {"main_city", "main_menu_city", "marksman_city_view"},
    "lancer_city_view": {"main_city", "main_menu_city"},
    "marksman_city_view": {"main_city", "main_menu_city", "arena_city_view", "fishing_main"},
}

# Hub screen ↔ survivor/worker list / exploration (Python tap graph; see navigation/edge_taps.yaml).
_MAIN_CITY_HUB_RAW: Final[dict[str, set[str]]] = {
    "main_city": {"survivor_status", "suggestion_box", "hero.recrutment", "exploration"},
    "survivor_status": {"main_city"},
    "suggestion_box": {"main_city"},
    "hero.recrutment": {"main_city"},
    "exploration": {"main_city", "squad_settings"},
    "squad_settings": {"exploration"},
}

# Network disconnect overlay: tapping `icon.reconnect` returns the client to main_city.
_RECONNECT_RAW: Final[dict[str, set[str]]] = {
    "reconnect": {"main_city"},
}

TUNDRA_ADVENTURE_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_TUNDRA_RAW)
MAIN_MENU_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_MAIN_MENU_RAW)
EXPLORATION_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_EXPLORATION_RAW)
CHAT_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_CHAT_RAW)
TROOPS_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_TROOPS_RAW)
MAIN_CITY_HUB_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_MAIN_CITY_HUB_RAW)
RECONNECT_EDGES: Final[dict[str, frozenset[str]]] = _freeze_adjacency(_RECONNECT_RAW)


def merge_topology_edges(
    *parts: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    """Union destinations for the same source across partial graphs."""
    merged: dict[str, set[str]] = {}
    for part in parts:
        for src, dsts in part.items():
            merged.setdefault(src, set()).update(dsts)
    return {s: frozenset(d) for s, d in merged.items()}


SCREEN_TOPOLOGY_EDGES: Final[dict[str, frozenset[str]]] = merge_topology_edges(
    TUNDRA_ADVENTURE_EDGES,
    MAIN_MENU_EDGES,
    EXPLORATION_EDGES,
    CHAT_EDGES,
    TROOPS_EDGES,
    MAIN_CITY_HUB_EDGES,
    RECONNECT_EDGES,
)
