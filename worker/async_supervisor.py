"""Single process: workers + scheduler share one asyncio loop.

Foreground/game-process checks run in a separate OS process — see ``worker.game_health_watchdog``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable

from config.loader import InstanceConfig, get_settings
from config.log_context import set_log_context
from config.logging_stdout import setup_stdout_logging
from config.startup_validation import assert_startup_configs_valid
from scheduler.ortools_executor import shutdown_ortools_executor
from worker.instance_worker import InstanceWorker
from worker.restart_backoff import compute_restart_delay

logger = logging.getLogger(__name__)

# A child that ran longer than ``base * _STABILITY_FACTOR`` before crashing is
# treated as stable; the next failure resets the backoff counter. Keeps a slow
# backoff from sticking around after one unrelated transient crash.
_STABILITY_FACTOR = 4


async def _guard_loop(name: str, run: Callable[[], Awaitable[None]]) -> None:
    base = float(get_settings().worker.restart_wait_seconds)
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception:
            ran_for = time.monotonic() - started
            if ran_for > base * _STABILITY_FACTOR:
                attempt = 1  # stabilized run — treat as a fresh failure
            else:
                attempt += 1
            delay = compute_restart_delay(attempt, base_seconds=base)
            logger.exception(
                "%s crashed after %.1fs (attempt=%d) — restart in %.1fs",
                name,
                ran_for,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)


async def _guarded_worker(inst: InstanceConfig) -> None:
    # contextvar binding lives for this asyncio Task — every log line from
    # inside this worker (and its child tasks) carries ``inst=<id>``.
    set_log_context(inst=inst.instance_id)

    async def _run() -> None:
        worker = InstanceWorker(inst)
        await worker.run()

    await _guard_loop(f"InstanceWorker {inst.instance_id}", _run)


async def _guarded_scheduler() -> None:
    async def _run() -> None:
        from scheduler.runner import SchedulerRunner

        runner = SchedulerRunner()
        await runner.run()

    await _guard_loop("Scheduler", _run)


async def run_forever_async(*, stop_event: threading.Event | None = None) -> None:
    """Start the scheduler and one async worker per instance."""
    setup_stdout_logging()
    logger.info(
        "wos: async supervisor running — worker/ADB/rolling logs follow on stdout "
        "(terminal where `uv run wos` is running)"
    )
    assert_startup_configs_valid()
    settings = get_settings()
    tasks = [
        asyncio.create_task(_guarded_worker(inst), name=f"worker-{inst.instance_id}")
        for inst in settings.instances
    ]
    tasks.append(asyncio.create_task(_guarded_scheduler(), name="scheduler"))
    try:
        if stop_event is None:
            await asyncio.gather(*tasks)
            return

        async def _wait_for_stop() -> None:
            await asyncio.to_thread(stop_event.wait)

        stop_task = asyncio.create_task(_wait_for_stop(), name="supervisor-stop-wait")
        try:
            done, pending = await asyncio.wait(
                [*tasks, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                logger.warning("wos: stop requested — cancelling workers and scheduler")
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                # A worker/scheduler finished unexpectedly; propagate (old behavior).
                await asyncio.gather(*tasks)
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
    finally:
        shutdown_ortools_executor(wait=False, cancel_futures=True)
