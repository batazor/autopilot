"""Single process: all workers and scheduler on one asyncio event loop (no multiprocessing)."""

from __future__ import annotations

import asyncio
import logging

from config.loader import InstanceConfig, get_settings
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


async def run_forever_async() -> None:
    """Start the scheduler and one async worker per instance."""
    settings = get_settings()
    tasks = [
        asyncio.create_task(_guarded_worker(inst), name=f"worker-{inst.instance_id}")
        for inst in settings.instances
    ]
    tasks.append(asyncio.create_task(_guarded_scheduler(), name="scheduler"))
    await asyncio.gather(*tasks)
