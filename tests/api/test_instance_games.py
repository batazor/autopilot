"""Regression tests for the ``/api/instances/games`` endpoint.

This endpoint previously called a non-existent ``devices_db.list_devices`` and,
because of a broad ``except: pass``, silently returned ``{}`` for every fleet —
so the per-instance game badges never populated. These tests pin the real
behaviour: a map keyed by device name with each device's resolved game.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.routers.instances import list_instance_games
from config.devices_db import set_device_game, upsert_device
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


class _FakeRedis:
    """Minimal stand-in: only ``hgetall`` is exercised via ``get_instance_state``."""

    def __init__(self, state: dict[str, dict[str, str]] | None = None) -> None:
        self._state = state or {}

    def hgetall(self, key: str) -> dict[str, str]:
        return self._state.get(key, {})


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_empty_registry_returns_empty_map(sqlite_db: Path) -> None:
    assert list_instance_games(_FakeRedis()) == {"games": {}}


def test_maps_device_name_to_game(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")  # defaults to wos
    upsert_device("bs2", adb_serial="127.0.0.1:5625")
    set_device_game("bs2", "kingshot")

    games = list_instance_games(_FakeRedis())["games"]

    assert games == {"bs1": "wos", "bs2": "kingshot"}


def test_keys_match_instance_ids(sqlite_db: Path) -> None:
    # The dashboard looks up games[instanceId] where instanceId comes from the
    # instances list — the keys must line up or every badge silently misses.
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    games = list_instance_games(_FakeRedis())["games"]
    assert "bs1" in games


def test_redis_running_game_overrides_profile(sqlite_db: Path) -> None:
    # The worker persists the game it actually runs into the instance state.
    # That live/last value must win over the static device-profile config so
    # the badge reflects what's running (or last ran), not the stale default.
    upsert_device("bs1", adb_serial="127.0.0.1:5555")  # profile defaults to wos
    redis = _FakeRedis({"wos:instance:bs1:state": {"game": "kingshot"}})

    games = list_instance_games(redis)["games"]

    assert games == {"bs1": "kingshot"}


def test_redis_unknown_game_falls_back_to_profile(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    redis = _FakeRedis({"wos:instance:bs1:state": {"game": "not-a-game"}})

    games = list_instance_games(redis)["games"]

    assert games == {"bs1": "wos"}
