"""Tests for the onboarding service (milestones + env health)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from api.services import onboarding
from config.devices_db import upsert_device
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


class _FakeRedis:
    """Minimal Redis stand-in covering just the methods onboarding.py uses."""

    def __init__(self, *, ping_raises: Exception | None = None) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._ping_raises = ping_raises

    def ping(self) -> bool:
        if self._ping_raises:
            raise self._ping_raises
        return True

    def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    def hsetnx(self, key: str, field: str, value: str) -> int:
        bucket = self._hashes.setdefault(key, {})
        if field in bucket:
            return 0
        bucket[field] = value
        return 1


@pytest.fixture
def devices_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "wos.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


@pytest.fixture
def bot_not_running() -> Any:
    with patch("worker.local_bot.bot_status", return_value={"running": False}):
        yield


@pytest.fixture
def bot_running() -> Any:
    with patch("worker.local_bot.bot_status", return_value={"running": True, "pid": 1234}):
        yield


def test_read_state_empty(devices_db: Path, bot_not_running: Any) -> None:
    client = _FakeRedis()
    state = onboarding.read_state(client)
    assert state == dict.fromkeys(onboarding.MILESTONES)


def test_read_state_detects_device_added(devices_db: Path, bot_not_running: Any) -> None:
    upsert_device("bs1", adb_serial="RF8RC00M8MF")
    client = _FakeRedis()
    state = onboarding.read_state(client)
    assert state["device_added_at"] is not None
    assert state["bot_started_at"] is None


def test_read_state_detects_bot_started(devices_db: Path, bot_running: Any) -> None:
    client = _FakeRedis()
    state = onboarding.read_state(client)
    assert state["bot_started_at"] is not None


def test_milestone_is_sticky(devices_db: Path, bot_not_running: Any) -> None:
    """Once a milestone is set, it persists even if the underlying signal disappears."""
    upsert_device("bs1", adb_serial="RF8RC00M8MF")
    client = _FakeRedis()
    first = onboarding.read_state(client)
    stamp = first["device_added_at"]
    assert stamp is not None

    # Wait one tick so timestamps would differ if the bit were overwritten.
    time.sleep(0.01)
    second = onboarding.read_state(client)
    assert second["device_added_at"] == stamp


def test_check_env_health_redis_ok() -> None:
    client = _FakeRedis()
    result = onboarding.check_env_health(client)
    assert result["redis"]["ok"] is True
    assert "latency_ms" in result["redis"]


def test_check_env_health_redis_failure() -> None:
    client = _FakeRedis(ping_raises=ConnectionError("connection refused"))
    result = onboarding.check_env_health(client)
    assert result["redis"]["ok"] is False
    assert "connection refused" in result["redis"]["error"]


def test_check_binary_not_found() -> None:
    result = onboarding._check_binary("definitely-not-a-real-tool", "", ["--version"])
    assert result["ok"] is False
    assert "not found" in result["error"]
