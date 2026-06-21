from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import worker.instance_worker as instance_worker
from scheduler.queue import QueueItem
from worker.onboarding_phase import ONBOARDING_EXIT_FIELD


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


def _identity_probe_worker(redis_async: Any, *, current_screen: str = "chapter") -> Any:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis_async
    worker._queue = _FakeQueue()

    async def _screen() -> str:
        return current_screen

    worker._instance_current_screen = _screen
    return worker


@pytest.mark.asyncio
async def test_who_i_am_enqueue_deferred_during_onboarding(redis_async: Any) -> None:
    """The identity probe stays out of the queue before Sawmill is built:
    gating at the push point, not via a scenario cond, avoids enqueue+bail spam."""
    worker = _identity_probe_worker(redis_async)

    # Sawmill unknown (not built / not recorded yet) → deferred.
    await worker._maybe_enqueue_who_i_am_when_active_player_missing()
    assert worker._queue.calls == []

    # Still onboarding (Sawmill recorded as not built) → deferred.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={ONBOARDING_EXIT_FIELD: "0"}
    )
    await worker._maybe_enqueue_who_i_am_when_active_player_missing()
    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_who_i_am_enqueue_when_main_city_even_without_sawmill_mirror(
    redis_async: Any,
) -> None:
    """Main city is enough to run the identity probe if the Sawmill mirror was missed."""
    worker = _identity_probe_worker(redis_async, current_screen="main_city")

    await worker._maybe_enqueue_who_i_am_when_active_player_missing()
    assert [c["task_type"] for c in worker._queue.calls] == ["who_i_am"]
    assert worker._queue.calls[0]["player_id"] == ""
    assert worker._queue.calls[0]["priority"] == 101_000


@pytest.mark.asyncio
async def test_who_i_am_enqueue_when_past_onboarding(redis_async: Any) -> None:
    """Built Sawmill with no active player → identity probe enqueues ``who_i_am``."""
    worker = _identity_probe_worker(redis_async)
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={ONBOARDING_EXIT_FIELD: "1"}
    )

    await worker._maybe_enqueue_who_i_am_when_active_player_missing()
    assert [c["task_type"] for c in worker._queue.calls] == ["who_i_am"]
    assert worker._queue.calls[0]["player_id"] == ""


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


class _FakeRedisState:
    """Minimal Redis stub exposing the one hash field the seed gate reads."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def hget(self, _key: str, field: str) -> str | None:
        return self._mapping.get(field)


@pytest.mark.asyncio
async def test_startup_seed_skipped_during_onboarding() -> None:
    """While ``active_player`` is "" (in-game onboarding/login phase), the boot
    seed must NOT publish ``check_main_city`` — navigating home fights the
    tutorial. Mirrors the cron's ``min_furnace_level`` gate."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._settings = SimpleNamespace(worker=SimpleNamespace())
    worker._queue = _FakeQueue()
    worker._redis = _FakeRedisState({"active_player": ""})

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    assert worker._queue.calls == []
    # Stale cleanup still runs even when nothing fresh is seeded.
    removed_types = {t for t, _ in worker._queue.removed}
    assert {"who_i_am", "check_main_city"} <= removed_types


@pytest.mark.asyncio
async def test_startup_seed_runs_for_identified_account() -> None:
    """With ``active_player`` already restored from the prior session, the seed
    publishes ``check_main_city`` so the bot routes home after restart."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._settings = SimpleNamespace(worker=SimpleNamespace())
    worker._queue = _FakeQueue()
    worker._redis = _FakeRedisState({"active_player": "765502864"})

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    assert [c["task_type"] for c in worker._queue.calls] == ["check_main_city"]
