from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import worker.instance_worker as instance_worker
from scheduler.queue import QueueItem


class _FakeRedis:
    async def hget(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


def _queue_item(task_type: str, *, player_id: str = "") -> QueueItem:
    return QueueItem(
        task_id="t1",
        player_id=player_id,
        task_type=task_type,
        priority=1,
        run_at=1.0,
        instance_id="bs1",
    )


@pytest.mark.asyncio
async def test_device_level_dsl_item_is_not_resolved_to_first_configured_player() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1", player_ids=["765502864"])
    worker._redis = _FakeRedis()

    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        worker,
        _queue_item("who_i_am"),
    )

    assert resolved.player_id == ""


@pytest.mark.asyncio
async def test_registered_device_task_still_resolves_to_known_player(monkeypatch: Any) -> None:
    class _RegisteredTask:
        pass

    monkeypatch.setitem(instance_worker._TASK_REGISTRY, "registered_task", _RegisteredTask)
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1", player_ids=["765502864"])
    worker._redis = _FakeRedis()

    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        worker,
        _queue_item("registered_task"),
    )

    assert resolved.player_id == "765502864"


@pytest.mark.asyncio
async def test_startup_identity_probe_is_enqueued_once_as_device_level() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1", player_ids=["765502864"])
    # _seed_startup_tasks reads grace/interval from settings to delay tasks.
    worker._settings = SimpleNamespace(
        worker=SimpleNamespace(
            overlay_analyze_after_launch_grace_seconds=0.0,
            device_reference_snapshot_interval_seconds=0.0,
        )
    )
    worker._queue = _FakeQueue()

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    assert [(call["task_type"], call["player_id"]) for call in worker._queue.calls] == [
        ("where_i_am", ""),
        ("who_i_am", ""),
    ]
    assert worker._queue.calls[0]["priority"] > worker._queue.calls[1]["priority"]
