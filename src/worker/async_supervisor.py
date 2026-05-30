"""Single process: workers + scheduler share one asyncio loop.

Foreground/game-process checks run in a separate OS process — see ``worker.game_health_watchdog``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from config.loader import (
    InstanceConfig,
    Settings,
    get_settings,
    load_settings,
    set_settings,
)
from config.log_context import set_log_context
from config.runtime_bootstrap import bootstrap_runtime_observability
from config.startup_validation import assert_startup_configs_valid
from dashboard.dashboard_events import DEVICE_RECONCILE_CHANNEL
from scheduler.ortools_executor import shutdown_ortools_executor
from services import (
    aclose_app_services,
    get_scheduler_runner,
    init_app_services,
    instance_worker_session,
)
from worker.health_watchdog_process import ensure_health_watchdog_process
from worker.restart_backoff import compute_restart_delay

if TYPE_CHECKING:
    import threading
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Periodic fallback cadence for the device-reconcile loop. Registration also
# publishes on ``DEVICE_RECONCILE_CHANNEL`` for instant pickup; this timeout
# catches missed pub/sub messages and out-of-band registry edits (e.g. removals
# that have no publish path yet).
_RECONCILE_POLL_SECONDS = 15.0

# A child that ran continuously for at least this long before crashing is
# treated as stable; the next failure resets the backoff counter. Keeps a slow
# backoff from sticking around after one unrelated transient crash. Kept well
# above realistic crash-loop periods so a worker that reliably dies shortly
# after the window can't reset to attempt=1 every cycle and dodge escalation.
_STABILITY_WINDOW_SECONDS = 300.0


async def _guard_loop(
    name: str,
    run: Callable[[], Awaitable[None]],
    *,
    settings: Settings,
) -> None:
    base = float(settings.worker.restart_wait_seconds)
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception:
            ran_for = time.monotonic() - started
            if ran_for > _STABILITY_WINDOW_SECONDS:
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


async def _guarded_worker(inst: InstanceConfig, settings: Settings) -> None:
    # contextvar binding lives for this asyncio Task — every log line from
    # inside this worker (and its child tasks) carries ``inst=<id>``.
    set_log_context(inst=inst.instance_id)

    async def _run() -> None:
        async with instance_worker_session(inst) as worker:
            await worker.run()

    await _guard_loop(
        f"InstanceWorker {inst.instance_id}",
        _run,
        settings=settings,
    )


async def _guarded_scheduler(settings: Settings) -> None:
    async def _run() -> None:
        runner = await get_scheduler_runner()
        await runner.run()

    await _guard_loop("Scheduler", _run, settings=settings)


def _read_fresh_settings() -> Settings:
    """Re-read the SQLite device registry and rebuild Settings.

    The device cache is invalidated first so a just-registered device shows up in
    ``settings.instances``. The caller decides whether to rebind the process-wide
    settings (only when the instance set actually changed).
    """
    from config.devices import invalidate_device_registry

    invalidate_device_registry()
    return load_settings()


async def _reconcile_once(workers: dict[str, asyncio.Task[None]]) -> None:
    """Bring the running worker set in line with the device registry.

    Spawns a guarded worker for every newly-registered device and cancels the
    worker of any device that disappeared. No-op (and no settings rebind) when
    the instance set is unchanged, so the periodic poll is cheap.
    """
    fresh = _read_fresh_settings()
    desired = {inst.instance_id: inst for inst in fresh.instances}
    if set(desired) == set(workers):
        return

    # Rebind the process-wide settings so worker/scheduler lookups by
    # instance_id see the new device set — but only on a real change.
    set_settings(fresh)

    for iid in list(workers):
        if iid not in desired:
            task = workers.pop(iid)
            task.cancel()
            # return_exceptions captures the worker's own CancelledError while
            # still propagating cancellation aimed at *this* reconcile task.
            await asyncio.gather(task, return_exceptions=True)
            logger.info("wos: device %s unregistered — worker stopped", iid)

    for iid, inst in desired.items():
        if iid not in workers:
            workers[iid] = asyncio.create_task(
                _guarded_worker(inst, fresh), name=f"worker-{iid}"
            )
            logger.info("wos: device %s registered — worker started", iid)


async def _reconcile_loop(workers: dict[str, asyncio.Task[None]], settings: Settings) -> None:
    """Event-driven + periodic reconcile of the device→worker set.

    Wakes immediately on a ``DEVICE_RECONCILE_CHANNEL`` publish (registration) and
    otherwise every ``_RECONCILE_POLL_SECONDS`` as a fallback. Survives Redis
    blips by reconnecting after a short backoff.
    """
    import redis.asyncio as aioredis

    while True:
        try:
            client = aioredis.from_url(settings.redis.url)
            pubsub = client.pubsub()
            await pubsub.subscribe(DEVICE_RECONCILE_CHANNEL)
            try:
                while True:
                    # Returns the message on a publish, or None on timeout —
                    # either way we reconcile against the registry.
                    await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=_RECONCILE_POLL_SECONDS,
                    )
                    try:
                        await _reconcile_once(workers)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("wos: device reconcile tick failed")
            finally:
                with suppress(Exception):
                    await pubsub.unsubscribe(DEVICE_RECONCILE_CHANNEL)
                with suppress(Exception):
                    await pubsub.aclose()
                with suppress(Exception):
                    await client.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "wos: device reconcile loop crashed — retry in %.1fs",
                _RECONCILE_POLL_SECONDS,
            )
            await asyncio.sleep(_RECONCILE_POLL_SECONDS)


async def run_forever_async(*, stop_event: threading.Event | None = None) -> None:
    """Start the scheduler and one async worker per instance."""
    bootstrap_runtime_observability("embedded")
    logger.info(
        "wos: async supervisor running — worker/ADB/rolling logs follow on stdout "
        "(terminal where `uv run play` is running)"
    )
    assert_startup_configs_valid()
    ensure_health_watchdog_process()
    await init_app_services()
    settings = get_settings()
    # Workers are keyed by instance_id so the reconcile loop can add/remove them
    # at runtime as devices are registered/unregistered.
    workers: dict[str, asyncio.Task[None]] = {
        inst.instance_id: asyncio.create_task(
            _guarded_worker(inst, settings),
            name=f"worker-{inst.instance_id}",
        )
        for inst in settings.instances
    }
    scheduler_task = asyncio.create_task(_guarded_scheduler(settings), name="scheduler")
    reconcile_task = asyncio.create_task(
        _reconcile_loop(workers, settings), name="device-reconcile"
    )
    # scheduler/reconcile run forever; workers are supervised by the reconcile
    # loop (spawn/cancel on registry change). We only block on the long-lived
    # tasks — a worker ending is either an intentional cancel (removed device)
    # or a guarded restart, neither of which should tear down the supervisor.
    longlived = [scheduler_task, reconcile_task]
    try:
        if stop_event is None:
            await asyncio.gather(*longlived)
            return

        async def _wait_for_stop() -> None:
            await asyncio.to_thread(stop_event.wait)

        stop_task = asyncio.create_task(_wait_for_stop(), name="supervisor-stop-wait")
        try:
            done, _pending = await asyncio.wait(
                [*longlived, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                logger.warning("wos: stop requested — cancelling workers and scheduler")
            else:
                # scheduler/reconcile finished unexpectedly; tear down the rest.
                logger.warning("wos: a supervisor task exited — shutting down")
            outstanding = [*longlived, *workers.values()]
            for t in outstanding:
                t.cancel()
            await asyncio.gather(*outstanding, return_exceptions=True)
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
    finally:
        shutdown_ortools_executor(wait=False, cancel_futures=True)
        await aclose_app_services()
