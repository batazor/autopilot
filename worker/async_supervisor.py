"""Single process: all workers and scheduler on one asyncio event loop (no multiprocessing)."""

from __future__ import annotations

import asyncio
import logging
import threading

from config.loader import InstanceConfig, get_settings
from config.logging_stdout import setup_stdout_logging
from worker.instance_worker import InstanceWorker

logger = logging.getLogger(__name__)


async def _guarded_worker(inst: InstanceConfig) -> None:
    delay = get_settings().worker.restart_wait_seconds
    while True:
        try:
            worker = InstanceWorker(inst)
            await worker.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("InstanceWorker %s crashed — restart in %ss", inst.instance_id, delay)
            await asyncio.sleep(delay)


async def _guarded_scheduler() -> None:
    delay = get_settings().worker.restart_wait_seconds
    while True:
        try:
            from scheduler.runner import SchedulerRunner

            runner = SchedulerRunner()
            await runner.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler crashed — restart in %ss", delay)
            await asyncio.sleep(delay)


async def run_forever_async(*, stop_event: threading.Event | None = None) -> None:
    """Start the scheduler and one async worker per instance."""
    setup_stdout_logging()
    logger.info(
        "wos: async supervisor running — worker/ADB/rolling logs follow on stdout "
        "(terminal where `uv run wos` is running)"
    )
    settings = get_settings()
    tasks = [
        asyncio.create_task(_guarded_worker(inst), name=f"worker-{inst.instance_id}")
        for inst in settings.instances
    ]
    tasks.append(asyncio.create_task(_guarded_scheduler(), name="scheduler"))
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
