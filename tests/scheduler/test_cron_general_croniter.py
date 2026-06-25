"""General (non-interval) cron scheduling via croniter.

The scheduler used to support only ``*/N * * * *`` and ``M */H * * *``; every
other shape (``0 * * * *`` hourly, ``0 4 * * *`` daily, …) silently never fired
even though such specs exist in the repo (read_calendar, scan_alliance_members,
read_bear_hunt, fishing_tournament). These cover the croniter-backed path that
replaced the broken minute-matcher, plus a repo-wide guard that every shipped
cron spec is actually schedulable.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

import scheduler.runner as runner_module
from config.paths import repo_root
from dsl.cron_specs import iter_cron_yaml_files_for_repo, load_root_mapping
from scheduler.queue import RedisQueue
from scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


def _local(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone()


# --------------------------------------------------------------------------- #
# _cron_next_run_at (pure, no Redis)
# --------------------------------------------------------------------------- #


def test_cron_next_run_at_hourly_lands_on_the_hour() -> None:
    after = time.time()
    nxt = SchedulerRunner._cron_next_run_at("0 * * * *", after=after)
    assert nxt is not None
    assert nxt > after
    assert nxt <= after + 3600 + 1
    dt = _local(nxt)
    assert (dt.minute, dt.second) == (0, 0)


def test_cron_next_run_at_daily_lands_at_local_0400() -> None:
    after = time.time()
    nxt = SchedulerRunner._cron_next_run_at("0 4 * * *", after=after)
    assert nxt is not None
    assert nxt > after
    assert nxt <= after + 24 * 3600 + 1
    dt = _local(nxt)
    assert (dt.hour, dt.minute, dt.second) == (4, 0, 0)


@pytest.mark.parametrize("expr", ["garbage", "0 99 * * *", "", "* * *", "*/0 * * * *"])
def test_cron_next_run_at_invalid_returns_none(expr: str) -> None:
    assert SchedulerRunner._cron_next_run_at(expr, after=time.time()) is None


def test_cron_next_run_at_strips_quotes() -> None:
    # YAML may carry the value already-unquoted, but be defensive about it.
    after = time.time()
    assert SchedulerRunner._cron_next_run_at('"0 4 * * *"', after=after) is not None


def test_all_repo_cron_specs_are_schedulable() -> None:
    """Every ``cron:`` spec the repo ships must be schedulable — either an
    interval fast-path or a croniter-valid expression. This is the guard that
    would have caught the original bug: ``0 4 * * *`` & friends parse fine but
    were dropped by the old two-shape matcher.
    """
    root = repo_root()
    unschedulable: list[tuple[str, str]] = []
    for yml in iter_cron_yaml_files_for_repo(root):
        raw = load_root_mapping(yml)
        if raw is None:
            continue
        expr = str(raw.get("cron") or "").strip()
        if not expr:
            continue
        fast_path = SchedulerRunner._cron_interval_seconds(expr) is not None
        if fast_path or SchedulerRunner._cron_next_run_at(expr, after=time.time()) is not None:
            continue
        unschedulable.append((yml.relative_to(root).as_posix(), expr))
    assert not unschedulable, f"cron specs that would never fire: {unschedulable}"


# --------------------------------------------------------------------------- #
# _ensure_cron_item_at (Redis-backed)
# --------------------------------------------------------------------------- #


def _make_runner(redis_async: aioredis.Redis, settings: Settings) -> SchedulerRunner:
    runner = SchedulerRunner(settings)
    runner._redis = redis_async
    runner._queue = RedisQueue(redis_async, settings)
    return runner


@pytest.mark.asyncio
async def test_ensure_cron_item_at_schedules_next_future_occurrence(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Daily cron, last run just now → a concrete item at the next 04:00 local
    (not 'fire iff we happen to tick at 04:00', which is what the old path did)."""
    runner = _make_runner(redis_async, settings)
    now = time.time()
    await runner._queue._append_recent_run(
        instance_id="bs1", task_type="read_calendar", player_id="p1", now=now
    )

    await runner._ensure_cron_item_at(
        name="read_calendar",
        spec_slug="read_calendar",
        expr="0 4 * * *",
        task_type="read_calendar",
        priority=100,
        instance_id="bs1",
        player_id="p1",
        now=now,
    )

    items = await runner._queue.peek_all()
    assert len(items) == 1
    run_at = items[0].run_at
    assert run_at > now
    assert run_at <= now + 24 * 3600 + 1
    assert (_local(run_at).hour, _local(run_at).minute) == (4, 0)


@pytest.mark.asyncio
async def test_ensure_cron_item_at_cold_start_runs_now(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """No history → debut immediately (don't make a fresh setup wait a day)."""
    runner = _make_runner(redis_async, settings)
    now = time.time()

    await runner._ensure_cron_item_at(
        name="read_bear_hunt",
        spec_slug="read_bear_hunt",
        expr="0 9 * * *",
        task_type="read_bear_hunt",
        priority=100,
        instance_id="bs_cold",
        player_id="p_cold",
        now=now,
    )

    items = await runner._queue.peek_all()
    assert len(items) == 1
    assert abs(items[0].run_at - now) < 5.0


@pytest.mark.asyncio
async def test_ensure_cron_item_at_overdue_runs_now(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Last run two days ago for a daily cron — overdue, so catch up now rather
    than waiting for the next 04:00."""
    runner = _make_runner(redis_async, settings)
    now = time.time()
    await runner._queue._append_recent_run(
        instance_id="bs_overdue",
        task_type="scan_members",
        player_id="p1",
        now=now - 2 * 86400,
    )

    await runner._ensure_cron_item_at(
        name="scan_members",
        spec_slug="scan_members",
        expr="0 4 * * *",
        task_type="scan_members",
        priority=100,
        instance_id="bs_overdue",
        player_id="p1",
        now=now,
    )

    items = await runner._queue.peek_all()
    assert len(items) == 1
    assert abs(items[0].run_at - now) < 5.0


@pytest.mark.asyncio
async def test_ensure_cron_item_at_invalid_expr_does_not_enqueue(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Defensive: an unparseable expr reaching the croniter branch enqueues
    nothing (the publish path is never entered)."""
    runner = _make_runner(redis_async, settings)
    now = time.time()
    await runner._queue._append_recent_run(
        instance_id="bs1", task_type="weird", player_id="p1", now=now - 100
    )

    await runner._ensure_cron_item_at(
        name="weird",
        spec_slug="weird",
        expr="not a cron",
        task_type="weird",
        priority=100,
        instance_id="bs1",
        player_id="p1",
        now=now,
    )

    assert await runner._queue.peek_all() == []


@pytest.mark.asyncio
async def test_ensure_cron_item_at_dedups_across_ticks(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Re-running the same spec on consecutive ticks keeps a single queue item."""
    runner = _make_runner(redis_async, settings)
    now = time.time()

    for _ in range(3):
        await runner._ensure_cron_item_at(
            name="read_calendar",
            spec_slug="read_calendar",
            expr="0 * * * *",
            task_type="read_calendar",
            priority=100,
            instance_id="bs1",
            player_id="p1",
            now=now,
        )

    items = await runner._queue.peek_all()
    assert len(items) == 1, [(i.task_id, i.task_type) for i in items]
    assert items[0].task_type == "read_calendar"


# --------------------------------------------------------------------------- #
# _run_cron_specs: invalid-cron logging
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_cron_specs_warns_once_on_invalid_cron(
    redis_async: aioredis.Redis,
    settings: Settings,
    tmp_path,
    caplog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spec whose cron neither matches a fast-path nor parses is logged once
    (per process) and skipped — not silently dropped, the original failure mode."""
    bad = tmp_path / "bad_cron.yaml"
    bad.write_text('cron: "every blue moon"\ntask: noop_invalid\n', encoding="utf-8")
    monkeypatch.setattr(
        runner_module, "iter_cron_yaml_files_for_repo", lambda *_a, **_k: [bad]
    )

    runner = _make_runner(redis_async, settings)

    with caplog.at_level(logging.WARNING, logger="scheduler.runner"):
        await runner._run_cron_specs()
        await runner._run_cron_specs()  # second tick must NOT re-warn

    warnings = [
        r for r in caplog.records
        if "unsupported/invalid cron expression" in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "every blue moon" in warnings[0].getMessage()
    assert await runner._queue.peek_all() == []


@pytest.mark.asyncio
async def test_run_cron_specs_enqueues_real_hourly_spec_end_to_end(
    redis_async: aioredis.Redis,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof against the real repo specs: ``read_calendar`` (cron
    ``0 * * * *``, no screen/furnace gate) now lands a concrete queue item.
    Before the fix this hourly spec was silently dropped by the two-shape
    matcher and never enqueued.
    """
    monkeypatch.setattr(
        runner_module, "player_ids_for_device_candidates", lambda *_a, **_k: ["p1"]
    )
    runner = _make_runner(redis_async, settings)
    now = time.time()

    await runner._run_cron_specs()

    items = await runner._queue.peek_all()
    by_type = {i.task_type: i for i in items}
    assert "read_calendar" in by_type, sorted(by_type)
    assert by_type["read_calendar"].run_at >= now - 2.0
