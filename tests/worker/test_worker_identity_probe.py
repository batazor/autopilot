from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import worker.instance_worker as instance_worker
from scheduler.queue import QueueItem


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.removed: list[tuple[str, str]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True

    async def remove_by_task_type(
        self, task_type: str, instance_id: str
    ) -> int:
        self.removed.append((task_type, instance_id))
        return 0


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
    worker._redis = None

    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        worker,
        _queue_item("who_i_am"),
    )

    assert resolved.player_id == ""


@pytest.mark.asyncio
async def test_registered_device_task_still_resolves_to_known_player(mocker) -> None:
    class _RegisteredTask:
        pass

    mocker.patch.dict(instance_worker._TASK_REGISTRY, {"registered_task": _RegisteredTask})
    # Production code resolves through player_ids_for_device_candidates (bluestacks
    # title + instance_id aliases); patch that hook so the test does not depend on
    # db/devices.yaml.
    mocker.patch.object(
        instance_worker,
        "player_ids_for_device_candidates",
        new=lambda *_names: ["765502864"],
    )
    mocker.patch.object(instance_worker, "player_ids_for_device", new=lambda _: ["765502864"])
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1", bluestacks_window_title="emulator-5554")
    worker._redis = None

    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        worker,
        _queue_item("registered_task"),
    )

    assert resolved.player_id == "765502864"


@pytest.mark.asyncio
async def test_startup_seed_does_not_enqueue_who_i_am() -> None:
    """``who_i_am`` is enqueued elsewhere (identity-probe), but the boot seed
    *does* publish ``check_main_city`` so the bot has a navigation goal after
    restart even when ``active_player`` is already populated from the prior
    session. See ``_STARTUP_SEED_TASKS``."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1", player_ids=["765502864"])
    worker._settings = SimpleNamespace(worker=SimpleNamespace())
    worker._queue = _FakeQueue()
    worker._redis = None

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    scheduled_types = [c["task_type"] for c in worker._queue.calls]
    assert "who_i_am" not in scheduled_types
    assert scheduled_types == ["check_main_city"]
    assert worker._queue.calls[0]["player_id"] == ""
    assert worker._queue.calls[0]["instance_id"] == "bs1"
    # Boot-time stale cleanup pass covers both seeded and identity types.
    removed_types = {t for t, _ in worker._queue.removed}
    assert {"who_i_am", "check_main_city"} <= removed_types
