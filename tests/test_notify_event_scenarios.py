"""EVENT_SCENARIOS seed map sanity (modules/notify/config.py).

A typo here fails silently at runtime — the queue just gets a scenario key the
worker can't resolve — so pin both sides of the mapping to reality.
"""
from __future__ import annotations

from config.paths import repo_root
from dsl.registry import iter_scenario_yaml_files
from modules.notify.config import EVENT_SCENARIOS, GAMES


def test_event_scenarios_reference_known_games_and_event_types() -> None:
    for game_id, mapping in EVENT_SCENARIOS.items():
        assert game_id in GAMES, f"unknown game {game_id!r} in EVENT_SCENARIOS"
        seeded_events = {event for event, _rx, _desc in GAMES[game_id].seed_patterns}
        for event_type in mapping:
            assert event_type in seeded_events, (
                f"{game_id}: event {event_type!r} has no seed pattern"
            )


def test_event_scenarios_point_at_existing_scenarios() -> None:
    stems = {p.stem for _root, p in iter_scenario_yaml_files(repo_root())}
    for game_id, mapping in EVENT_SCENARIOS.items():
        for event_type, key in mapping.items():
            assert key.strip(), f"{game_id}/{event_type}: empty scenario key"
            assert key in stems, (
                f"{game_id}/{event_type}: scenario {key!r} not found in catalog"
            )
