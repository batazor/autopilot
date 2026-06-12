"""Tests for SchedulerRunner._run_gift_codes_polling.

The scheduler drives the gift-codes global poller so we don't fan out a
no-op redeem per (device × player). These tests use the testcontainer
Redis and stub ``importlib`` so we never hit external HTTP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from dsl.cron_specs import scenario_loader_paths
from dsl.evaluator import ScenarioEvaluator
from dsl.loader import ScenarioLoader
from scheduler.optimizer import TaskOptimizer
from scheduler.runner import (
    _GIFT_CODE_GAMES,
    _GIFT_CODE_LOCK_TTL_S,
    _GIFT_CODE_POLL_INTERVAL_S,
    SchedulerRunner,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


def _make_runner(settings: Settings) -> SchedulerRunner:
    repo_root = Path(__file__).resolve().parents[2]
    return SchedulerRunner(
        settings,
        ScenarioLoader(scenario_loader_paths(repo_root)),
        TaskOptimizer(settings),
        ScenarioEvaluator(),
    )


def _fake_summary(total: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        results=[None] * total,
        counts_by_status=lambda: {"SUCCESS": total},
    )


def _fake_importlib(scrape_codes: list[str], total: int) -> MagicMock:
    """Build an ``importlib``-shaped mock with ``poll_once`` and
    ``run_gift_code_redeemer`` async functions."""
    fake_mod = SimpleNamespace(
        poll_once=AsyncMock(return_value=scrape_codes),
        run_gift_code_redeemer=AsyncMock(return_value=_fake_summary(total=total)),
    )
    importlib_mod = MagicMock()
    importlib_mod.import_module = MagicMock(return_value=fake_mod)
    return importlib_mod


async def _wait_for_background_tasks() -> None:
    """Yield until all named gift-codes background tasks finish."""
    for _ in range(50):
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("gift-codes-poll-") and not t.done()
        ]
        if not pending:
            return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_gift_codes_polling_runs_once_per_game(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """First call should fire one scrape+redeem per registered game and
    leave the cadence key armed for ``_GIFT_CODE_POLL_INTERVAL_S``."""
    runner = _make_runner(settings)
    runner._redis = redis_async  # type: ignore[assignment]

    fake = _fake_importlib(scrape_codes=["AAA", "BBB"], total=3)
    monkeypatch.setattr("scheduler.runner.importlib", fake)

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()

    # One import per game.
    import_calls = [c.args[0] for c in fake.import_module.call_args_list]
    assert sorted(import_calls) == sorted(g[1] for g in _GIFT_CODE_GAMES)

    # Cadence keys are set with the long TTL — so the next tick within
    # the 6h window will see them and skip.
    for game_id, _path, _lock, _redeem_supported in _GIFT_CODE_GAMES:
        ttl = await redis_async.ttl(f"wos:scheduler:gift_codes_poll:{game_id}")
        assert ttl > 0
        assert ttl <= _GIFT_CODE_POLL_INTERVAL_S


@pytest.mark.asyncio
async def test_gift_codes_polling_second_tick_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """Two ticks inside the 6h window should yield exactly one cycle per
    game — the cadence key blocks the second tick."""
    runner = _make_runner(settings)
    runner._redis = redis_async  # type: ignore[assignment]

    fake = _fake_importlib(scrape_codes=[], total=0)
    monkeypatch.setattr("scheduler.runner.importlib", fake)

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()
    first_import_count = fake.import_module.call_count

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()

    assert fake.import_module.call_count == first_import_count


@pytest.mark.asyncio
async def test_gift_codes_polling_skips_when_redeem_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """A manual UI redeem already in-flight holds the redeem lock; the
    scheduler must defer and try again next cadence window."""
    runner = _make_runner(settings)
    runner._redis = redis_async  # type: ignore[assignment]

    # Simulate the UI exec handler holding the WOS lock; Kingshot is free.
    wos_lock = next(g[2] for g in _GIFT_CODE_GAMES if g[0] == "wos")
    await redis_async.set(wos_lock, "ui:held", ex=_GIFT_CODE_LOCK_TTL_S)

    fake = _fake_importlib(scrape_codes=[], total=0)
    monkeypatch.setattr("scheduler.runner.importlib", fake)

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()

    # Only Kingshot ran; WOS was blocked by the foreign lock.
    import_calls = [c.args[0] for c in fake.import_module.call_args_list]
    assert "century.gift_codes.kingshot" in import_calls
    assert "century.gift_codes.wos" not in import_calls

    # WOS cadence key did get set (acquired before the redeem-lock check),
    # so the scheduler won't keep retrying every 30s — it waits the full
    # 6h window before another attempt. Verify that's the case.
    ttl = await redis_async.ttl("wos:scheduler:gift_codes_poll:wos")
    assert ttl > 0


@pytest.mark.asyncio
async def test_gift_codes_polling_beta_scrapes_without_redeem_lock(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """Beta code discovery is Discord scrape-only; beta redeem is manual in game."""
    runner = _make_runner(settings)
    runner._redis = redis_async  # type: ignore[assignment]

    fake = _fake_importlib(scrape_codes=["BETA"], total=0)
    monkeypatch.setattr("scheduler.runner.importlib", fake)

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()

    for game_id, _path, lock_key, redeem_supported in _GIFT_CODE_GAMES:
        held = await redis_async.get(lock_key)
        if redeem_supported:
            continue
        assert game_id.endswith("_beta")
        assert held is None


@pytest.mark.asyncio
async def test_gift_codes_polling_releases_redeem_lock_only_if_owned(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    """If a manual trigger races in and overwrites the lock value while
    our background task runs, we must not delete their lock on exit."""
    runner = _make_runner(settings)
    runner._redis = redis_async  # type: ignore[assignment]

    wos_lock = next(g[2] for g in _GIFT_CODE_GAMES if g[0] == "wos")

    async def _swap_lock_then_finish() -> SimpleNamespace:
        # Mimic a parallel UI manual redeem stomping the lock value
        # before our task completes.
        await redis_async.set(wos_lock, "ui:stomp", ex=_GIFT_CODE_LOCK_TTL_S)
        return _fake_summary(total=0)

    fake_mod = SimpleNamespace(
        poll_once=AsyncMock(return_value=[]),
        run_gift_code_redeemer=AsyncMock(side_effect=_swap_lock_then_finish),
    )
    importlib_mod = MagicMock()
    importlib_mod.import_module = MagicMock(return_value=fake_mod)
    monkeypatch.setattr("scheduler.runner.importlib", importlib_mod)

    await runner._run_gift_codes_polling()
    await _wait_for_background_tasks()

    # The lock should still be the stomped value, not deleted.
    held = await redis_async.get(wos_lock)
    assert held is not None
    held_s = held.decode() if isinstance(held, bytes) else held
    assert held_s == "ui:stomp"
