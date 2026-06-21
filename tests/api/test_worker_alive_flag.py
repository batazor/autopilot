"""`worker_alive` derivation for the approval preview placeholder.

Distinguishes "bot running, capture warming up" from "bot stopped" using the
worker's ``last_seen_at`` heartbeat freshness.
"""
from __future__ import annotations

import time

from api.services import click_approval_store as store


class _FakeRedis:
    def __init__(
        self,
        *,
        kv: dict[str, str] | None = None,
        hashes: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.kv = kv or {}
        self.hashes = hashes or {}
        self.set_calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self.kv[key] = value


def test_fresh_heartbeat_is_alive() -> None:
    assert store._worker_recently_seen({"last_seen_at": str(time.time())}) is True


def test_stale_heartbeat_is_not_alive() -> None:
    old = time.time() - store._WORKER_ALIVE_WINDOW_S - 5.0
    assert store._worker_recently_seen({"last_seen_at": str(old)}) is False


def test_missing_heartbeat_is_not_alive() -> None:
    assert store._worker_recently_seen({}) is False


def test_unparseable_heartbeat_is_not_alive() -> None:
    assert store._worker_recently_seen({"last_seen_at": ""}) is False
    assert store._worker_recently_seen({"last_seen_at": "not-a-number"}) is False


def test_boundary_just_inside_window_is_alive() -> None:
    recent = time.time() - (store._WORKER_ALIVE_WINDOW_S - 1.0)
    assert store._worker_recently_seen({"last_seen_at": str(recent)}) is True


def test_approval_status_does_not_refresh_ui_heartbeat() -> None:
    now = str(time.time())
    client = _FakeRedis(
        kv={
            "wos:ui:click_approval:enabled:bs1": "1",
            "wos:ui:click_approval:heartbeat:bs1": now,
        },
        hashes={
            "wos:instance:bs1:state": {
                "last_seen_at": now,
                "current_screen": "main_city",
                "active_player": "player-a",
            },
            "wos:player:player-a:state": {"player_id": "12345"},
        },
    )

    status = store.get_approval_status(client, "bs1")

    assert status["approval_enabled"] is True
    assert status["heartbeat_active"] is True
    assert status["worker_alive"] is True
    assert status["current_screen"] == "main_city"
    assert status["active_player_in_game_id"] == "12345"
    assert client.set_calls == []
