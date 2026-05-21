"""Login ads (phase 1) block ``who_i_am`` (phase 2) until the queue drains."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import worker.instance_worker as instance_worker
from analysis.login_ads import login_ad_task_types
from config.paths import repo_root
from worker.instance_worker_redis import (
    _LOGIN_AD_SETTLE_S,
    _WHO_I_AM_BOOT_GRACE_S,
)


class _FakeQueue:
    def __init__(self, *, pending_types: frozenset[str] = frozenset()) -> None:
        self._pending_types = pending_types
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


def _worker(
    *,
    pending_types: frozenset[str] = frozenset(),
    running_type: str = "",
    current_scenario: str = "",
    active_player: str = "",
) -> instance_worker.InstanceWorker:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._stopping = False
    worker._ui_paused = False
    worker._queue = _FakeQueue(pending_types=pending_types)

    async def _hget(_key: str, field: str) -> bytes | None:
        if field == "active_player":
            return active_player.encode() if active_player else b""
        if field == "current_scenario":
            return current_scenario.encode() if current_scenario else b""
        return None

    async def _get(key: str) -> bytes | None:
        if key == "wos:queue:running:bs1" and running_type:
            return json.dumps({"task_type": running_type}).encode()
        return None

    async def _zrangebyscore(_key: str, _lo: str, _hi: str) -> list[bytes]:
        return [
            json.dumps(
                {
                    "task_type": t,
                    "instance_id": "bs1",
                    "player_id": "",
                }
            ).encode()
            for t in pending_types
        ]

    redis = SimpleNamespace(
        hget=AsyncMock(side_effect=_hget),
        get=AsyncMock(side_effect=_get),
        zrangebyscore=AsyncMock(side_effect=_zrangebyscore),
    )
    worker._redis = redis
    worker._worker_boot_at = time.monotonic() - _WHO_I_AM_BOOT_GRACE_S - 1.0
    worker._boot_interactive_at = time.monotonic() - _WHO_I_AM_BOOT_GRACE_S - 1.0
    worker._last_login_ad_finished_at = 0.0
    return worker


@pytest.mark.asyncio
async def test_login_ads_phase_active_when_pending_myriad_bazaar() -> None:
    worker = _worker(pending_types=frozenset({"myriad_bazaar"}))
    assert await instance_worker.InstanceWorker._login_ads_phase_active(worker) is True


@pytest.mark.asyncio
async def test_login_ads_phase_inactive_when_queue_empty() -> None:
    worker = _worker()
    assert await instance_worker.InstanceWorker._login_ads_phase_active(worker) is False


@pytest.mark.asyncio
async def test_who_i_am_not_enqueued_while_login_ads_pending() -> None:
    worker = _worker(pending_types=frozenset({"ads_natalia"}))
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_enqueued_after_login_ads_drain() -> None:
    worker = _worker()
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert len(worker._queue.calls) == 1
    assert worker._queue.calls[0]["task_type"] == "who_i_am"
    assert worker._queue.calls[0]["player_id"] == ""


@pytest.mark.asyncio
async def test_startup_seed_does_not_enqueue_who_i_am() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._queue = _FakeQueue()
    worker._redis = None

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_deferred_during_boot_grace_when_no_login_ad_yet() -> None:
    worker = _worker()
    worker._worker_boot_at = time.monotonic()
    worker._boot_interactive_at = time.monotonic()
    worker._last_login_ad_finished_at = 0.0
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_deferred_for_settle_after_login_ad_finishes() -> None:
    worker = _worker()
    worker._worker_boot_at = time.monotonic() - 1.0
    worker._boot_interactive_at = time.monotonic() - 1.0
    worker._last_login_ad_finished_at = time.monotonic()
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_allowed_after_settle_since_last_login_ad() -> None:
    worker = _worker()
    worker._worker_boot_at = time.monotonic() - 10.0
    worker._boot_interactive_at = time.monotonic() - 10.0
    worker._last_login_ad_finished_at = time.monotonic() - _LOGIN_AD_SETTLE_S - 0.5
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls[0]["task_type"] == "who_i_am"


@pytest.mark.asyncio
async def test_who_i_am_deferred_while_current_screen_is_loading() -> None:
    worker = _worker()
    worker._boot_interactive_at = time.monotonic() - _WHO_I_AM_BOOT_GRACE_S - 1.0

    async def _hget(_key: str, field: str) -> bytes | None:
        if field == "current_screen":
            return b"loading"
        return b""

    worker._redis.hget = AsyncMock(side_effect=_hget)
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_deferred_until_first_non_loading_screen() -> None:
    worker = _worker()
    worker._boot_interactive_at = 0.0
    await instance_worker.InstanceWorker._maybe_enqueue_who_i_am_when_active_player_missing(
        worker
    )
    assert worker._queue.calls == []


def test_login_ad_task_types_discovered_from_ads_overlay() -> None:
    """New login popups only need scenario YAML + overlay rule (no worker edit)."""
    assert login_ad_task_types(repo_root()) == frozenset({
        "myriad_bazaar",
        "ads_natalia",
        "ads_rookie_value_pack",
        "tap_ads_legend_transcend_pack",
    })
