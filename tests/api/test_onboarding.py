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
        self._lists: dict[str, list[str]] = {}
        self._kv: dict[str, str] = {}
        self._ping_raises = ping_raises

    def ping(self) -> bool:
        if self._ping_raises:
            raise self._ping_raises
        return True

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

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

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._lists.get(key, [])
        return list(items[start : end + 1])

    def seed_list(self, key: str, items: list[str]) -> None:
        self._lists[key] = list(items)

    def seed_hash(self, key: str, mapping: dict[str, str]) -> None:
        self._hashes[key] = dict(mapping)

    def seed_kv(self, key: str, value: str) -> None:
        self._kv[key] = value


class _UnavailableRedis:
    """Redis stand-in that raises for every command used by onboarding."""

    _ERROR = "redis unavailable"

    def get(self, key: str) -> str | None:
        raise ConnectionError(self._ERROR)

    def hget(self, key: str, field: str) -> str | None:
        raise ConnectionError(self._ERROR)

    def hgetall(self, key: str) -> dict[str, str]:
        raise ConnectionError(self._ERROR)

    def hsetnx(self, key: str, field: str, value: str) -> int:
        raise ConnectionError(self._ERROR)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        raise ConnectionError(self._ERROR)


@pytest.fixture
def devices_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
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


def test_read_state_degrades_when_redis_unavailable(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    state = onboarding.read_state(_UnavailableRedis())
    assert state == dict.fromkeys(onboarding.MILESTONES)


def test_read_state_decodes_byte_hashes(devices_db: Path, bot_not_running: Any) -> None:
    client = _FakeRedis()
    client._hashes[onboarding.ONBOARDING_KEY] = {
        b"first_scenario_at": b"2026-01-01T00:00:00Z",
    }
    state = onboarding.read_state(client)
    assert state["first_scenario_at"] == "2026-01-01T00:00:00Z"


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


@pytest.fixture
def instances() -> Any:
    with patch(
        "api.services.instances.list_instance_ids",
        return_value=["bs1", "bs2"],
    ):
        yield


def test_scenario_milestone_set_when_history_has_success(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_list(
        "wos:queue:history:bs1",
        ['{"success": true, "scenario": "claim_mail"}'],
    )
    state = onboarding.read_state(client)
    assert state["first_scenario_at"] is not None


def test_scenario_milestone_ignores_failed_entries(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_list(
        "wos:queue:history:bs1",
        [
            '{"success": false, "scenario": "claim_mail", "error": "timeout"}',
            "not json at all",
        ],
    )
    state = onboarding.read_state(client)
    assert state["first_scenario_at"] is None


def test_ocr_milestone_set_when_state_has_text(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_hash(
        "wos:instance:bs1:state",
        {
            "dsl_last_ocr_at": "1700000000",
            "dsl_last_ocr_raw_text": "Level 25",
        },
    )
    state = onboarding.read_state(client)
    assert state["first_ocr_at"] is not None


def test_ocr_milestone_skipped_when_text_empty(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_hash(
        "wos:instance:bs1:state",
        {
            "dsl_last_ocr_at": "1700000000",
            "dsl_last_ocr_raw_text": "",
            "dsl_last_ocr_value": "",
        },
    )
    state = onboarding.read_state(client)
    assert state["first_ocr_at"] is None


def test_approvals_disabled_milestone_set_when_all_instances_off(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_kv("wos:ui:click_approval:enabled:bs1", "0")
    client.seed_kv("wos:ui:click_approval:enabled:bs2", "off")
    state = onboarding.read_state(client)
    assert state["approvals_disabled_at"] is not None


def test_approvals_disabled_milestone_skipped_when_one_still_on(
    devices_db: Path, bot_not_running: Any, instances: Any
) -> None:
    client = _FakeRedis()
    client.seed_kv("wos:ui:click_approval:enabled:bs1", "0")
    # bs2 left unset → defaults to enabled
    state = onboarding.read_state(client)
    assert state["approvals_disabled_at"] is None


def test_approvals_disabled_milestone_skipped_without_instances(
    devices_db: Path, bot_not_running: Any
) -> None:
    client = _FakeRedis()
    state = onboarding.read_state(client)
    assert state["approvals_disabled_at"] is None
