from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import redis.asyncio as aioredis

from config.loader import Settings
from scenarios.cron_specs import scenario_loader_paths
from scenarios.evaluator import ScenarioEvaluator
from scenarios.loader import ScenarioLoader
from scheduler.optimizer import TaskOptimizer
from scheduler.queue import RedisQueue
from scheduler.runner import SchedulerRunner


def _make_scheduler_runner(settings: Settings) -> SchedulerRunner:
    repo_root = Path(__file__).resolve().parents[2]
    return SchedulerRunner(
        settings,
        ScenarioLoader(scenario_loader_paths(repo_root)),
        TaskOptimizer(settings),
        ScenarioEvaluator(),
    )


def _wire_runner(
    runner: SchedulerRunner,
    *,
    settings: Settings,
    redis_client: aioredis.Redis,
    monkeypatch: pytest.MonkeyPatch,
    tasks: list[SimpleNamespace],
) -> RedisQueue:
    """Bind real Redis + ``RedisQueue`` and stub the I/O-heavy seams.

    The optimizer, scenario loader, and player-state lookups are replaced
    with deterministic stubs so the test focuses on the enqueue gate
    (pending dedup + running-task dedup). The queue side is the real
    ``RedisQueue`` against the testcontainer Redis — exercises payload
    serialization, dedup ZADD script, and ``peek_all`` end-to-end.
    """
    queue = RedisQueue(redis_client, settings)
    runner._queue = queue  # type: ignore[assignment]
    runner._redis = redis_client  # type: ignore[assignment]

    async def _no_cron() -> None:
        return None

    async def _player_states() -> dict[str, dict[str, object]]:
        return {"p1": {"player_id": "p1"}}

    async def _player_instance_map() -> dict[str, str]:
        return {"p1": "bs1"}

    async def _active_scenario_id(player_id: str) -> str | None:
        return None

    runner._run_cron_specs = _no_cron  # type: ignore[assignment]
    runner._load_player_states = _player_states  # type: ignore[assignment]
    runner._build_player_instance_map = _player_instance_map  # type: ignore[assignment]
    runner._active_scenario_id = _active_scenario_id  # type: ignore[assignment]

    monkeypatch.setattr(runner._scenario_loader, "load_all", lambda: [])

    async def _fake_executor(_loop: Any, _func: Any, _inp: Any) -> dict[str, list[Any]]:
        return {"p1": list(tasks)}

    monkeypatch.setattr("scheduler.runner.run_in_ortools_executor", _fake_executor)
    return queue


@pytest.mark.asyncio
async def test_scheduler_enqueues_optimizer_assignments(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """Happy path: optimizer assigns a task → ``_run_once`` lands it in Redis
    under ``wos:queue:bs1`` with the expected fields."""
    runner = _make_scheduler_runner(settings)
    queue = _wire_runner(
        runner,
        settings=settings,
        redis_client=redis_async,
        monkeypatch=monkeypatch,
        tasks=[
            SimpleNamespace(task_id="t1", task_type="check_main_city", priority=10),
        ],
    )

    await runner._run_once()

    items = await queue.peek_all()
    assert len(items) == 1
    item = items[0]
    assert item.task_id == "t1"
    assert item.task_type == "check_main_city"
    assert item.player_id == "p1"
    assert item.instance_id == "bs1"
    assert item.priority == 10


@pytest.mark.asyncio
async def test_scheduler_dedups_pending_duplicate_across_ticks(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """Repeated ticks with the same optimizer output don't pile up duplicates
    in the pending queue. This is the ``skip_if_duplicate`` half of the gate:
    once an item is queued and not yet popped, the next tick is a no-op."""
    runner = _make_scheduler_runner(settings)
    queue = _wire_runner(
        runner,
        settings=settings,
        redis_client=redis_async,
        monkeypatch=monkeypatch,
        tasks=[
            SimpleNamespace(task_id="t1", task_type="check_main_city", priority=10),
        ],
    )

    await runner._run_once()
    await runner._run_once()
    await runner._run_once()

    items = await queue.peek_all()
    assert len(items) == 1, [(i.task_id, i.task_type) for i in items]


@pytest.mark.asyncio
async def test_scheduler_skips_enqueue_when_task_already_running(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """``skip_if_duplicate`` only looks at the pending queue. Once the worker
    pops a long-running item the queue is empty and a fresh scheduler tick
    would enqueue a second copy. ``_run_once`` must also consult the
    per-instance running key (``wos:queue:running:{instance_id}``) and skip
    matching tasks that are already in flight.
    """
    # Simulate the running key that ``worker/instance_worker_tasks.py`` writes
    # the moment it pops a task — same payload shape, same TTL semantics.
    await redis_async.set(
        "wos:queue:running:bs1",
        json.dumps(
            {
                "task_id": "in-flight",
                "task_type": "claim_trials",
                "player_id": "p1",
                "instance_id": "bs1",
            }
        ),
        ex=180,
    )

    runner = _make_scheduler_runner(settings)
    queue = _wire_runner(
        runner,
        settings=settings,
        redis_client=redis_async,
        monkeypatch=monkeypatch,
        tasks=[
            SimpleNamespace(task_id="t-running", task_type="claim_trials", priority=10),
            SimpleNamespace(task_id="t-other", task_type="check_main_city", priority=10),
        ],
    )

    await runner._run_once()

    # ``claim_trials`` is skipped (running on bs1 for p1); ``check_main_city``
    # is still enqueued because its task_type doesn't match.
    items = await queue.peek_all()
    queued_types = sorted(i.task_type for i in items)
    assert queued_types == ["check_main_city"], [(i.task_id, i.task_type) for i in items]


@pytest.mark.asyncio
async def test_scheduler_does_not_skip_when_running_task_is_different_type(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """Running-task gate must match by ``task_type``. A different running task
    on the same instance shouldn't block unrelated assignments."""
    await redis_async.set(
        "wos:queue:running:bs1",
        json.dumps(
            {
                "task_id": "in-flight",
                "task_type": "claim_trials",
                "player_id": "p1",
                "instance_id": "bs1",
            }
        ),
        ex=180,
    )

    runner = _make_scheduler_runner(settings)
    queue = _wire_runner(
        runner,
        settings=settings,
        redis_client=redis_async,
        monkeypatch=monkeypatch,
        tasks=[
            SimpleNamespace(task_id="t-other", task_type="check_main_city", priority=10),
        ],
    )

    await runner._run_once()

    items = await queue.peek_all()
    assert len(items) == 1
    assert items[0].task_type == "check_main_city"
