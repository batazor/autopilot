"""Durable ``active_player`` persistence across worker restarts & game relaunches.

Covers:
- ``InstanceWorkerRedisMixin._connect`` restoring ``active_player`` from the
  durable device registry (so ``who_i_am`` is skipped on a worker restart).
- ``InstanceWorkerHealthMixin._restart_instance`` clearing ``active_player`` on a
  game relaunch (so identity is re-verified lazily after a possible account switch).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.devices_db import set_last_active_player, upsert_device
from config.loader import (
    InstanceConfig,
    OcrConfig,
    RedisConfig,
    SchedulerConfig,
    Settings,
    WorkerConfig,
)
from config.state_sqlite import set_state_db_path_for_tests
from navigation.lifecycle_states import InstanceState
from worker.instance_worker_health import InstanceWorkerHealthMixin
from worker.instance_worker_redis import InstanceWorkerRedisMixin

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def _settings(url: str) -> Settings:
    return Settings(
        redis=RedisConfig(url=url),
        ocr=OcrConfig(),
        scheduler=SchedulerConfig(),
        worker=WorkerConfig(),
        instances=[
            InstanceConfig(instance_id="bs1", bluestacks_window_title="127.0.0.1:5555"),
        ],
    )


class _RedisWorker(InstanceWorkerRedisMixin):
    def __init__(self, redis_client: Any, settings: Settings) -> None:
        self._cfg = SimpleNamespace(
            instance_id="bs1",
            bluestacks_window_title="127.0.0.1:5555",
        )
        self._settings = settings
        self._redis = redis_client
        self._queue = None
        self._claims = None
        self._owns_redis = False


# ---------------------------------------------------------------------------
# restore on worker boot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_restores_active_player_from_durable_store(
    redis_async: Any, sqlite_db: Path
) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    set_last_active_player("bs1", "401227964")
    worker = _RedisWorker(redis_async, _settings("redis://unused"))

    await worker._connect()

    stored = await redis_async.hget("wos:instance:bs1:state", "active_player")
    assert stored == "401227964"
    assert await redis_async.hget("wos:instance:bs1:state", "active_player_at")


@pytest.mark.asyncio
async def test_connect_restores_by_adb_serial(
    redis_async: Any, sqlite_db: Path
) -> None:
    # Stored under the friendly name; worker also passes the serial as a candidate.
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    set_last_active_player("bs1", "555")
    worker = _RedisWorker(redis_async, _settings("redis://unused"))

    await worker._connect()

    assert await redis_async.hget("wos:instance:bs1:state", "active_player") == "555"


@pytest.mark.asyncio
async def test_connect_leaves_active_player_empty_when_nothing_stored(
    redis_async: Any, sqlite_db: Path
) -> None:
    worker = _RedisWorker(redis_async, _settings("redis://unused"))

    await worker._connect()

    assert await redis_async.hget("wos:instance:bs1:state", "active_player") == ""


# ---------------------------------------------------------------------------
# clear on game relaunch
# ---------------------------------------------------------------------------


class _HealthWorker(InstanceWorkerHealthMixin):
    _POST_RESTART_GRACE_S = 0.0
    _FOREGROUND_VERIFY_TIMEOUT_S = 5.0
    _FOREGROUND_VERIFY_INTERVAL_S = 0.0

    def __init__(self) -> None:
        self._cfg = SimpleNamespace(
            instance_id="bs1",
            bluestacks_window_title="127.0.0.1:5555",
        )
        self._ui_paused = False
        self._redis = AsyncMock()
        self._bot_actions = MagicMock()
        self._bot_actions.is_game_running.return_value = True
        self.states: list[Any] = []

    async def _run_blocking(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    async def _set_instance_state(self, state: Any, *, error: str = "") -> None:
        self.states.append(state)

    async def _cancel_current_task(self, *_a: Any, **_k: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_restart_instance_clears_active_player() -> None:
    worker = _HealthWorker()

    with patch("worker.instance_worker_health.asyncio.sleep", new=AsyncMock()):
        await worker._restart_instance()

    # active_player blanked on the instance hash so who_i_am re-arms next tick.
    worker._redis.hset.assert_any_call(
        "wos:instance:bs1:state", "active_player", ""
    )
    assert InstanceState.READY in worker.states
