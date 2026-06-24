from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
from contextlib import suppress as _suppress
from dataclasses import dataclass
from typing import Any

from config import telemetry
from config.loader import InstanceConfig, get_settings, load_settings, set_settings
from config.runtime_bootstrap import (
    bootstrap_runtime_observability,
    shutdown_runtime_observability,
)
from config.startup_validation import assert_startup_configs_valid
from worker.health_server import start_health_server
from worker.health_watchdog_process import (
    ensure_health_watchdog_process,
    stop_health_watchdog_process,
)
from worker.orphan_helpers import cleanup_orphaned_sck_capture_helpers
from worker.restart_backoff import compute_restart_delay

# Loopback HTTP ``/health`` port for the headless supervisor. Override with
# ``WOS_BOT_HEALTH_PORT``; keep the container healthcheck's port in sync.
_DEFAULT_HEALTH_PORT = 8770

logger = logging.getLogger(__name__)

# Base delay; exponential backoff + jitter applied on top via
# ``compute_restart_delay``. Matches the embedded supervisor's behavior so
# operators see consistent restart timings between deployments.
_BASE_RESTART_DELAY_SECONDS = 10.0
# A child process that ran continuously for at least this long is treated as
# stabilized — the next failure resets its backoff counter. This must stay well
# above realistic crash-loop periods: if it's only a few multiples of the base
# delay, a worker that reliably dies just after the window (e.g. a periodic OOM
# every ~45s) resets to attempt=1 every cycle and never escalates its backoff.
_STABILITY_WINDOW_SECONDS = 300.0
# The reconcile loop ticks ~1/s; if its last tick is older than this the
# supervisor is considered hung and ``/health`` reports 503. Generous vs the
# 1s cadence so a brief stall (e.g. a slow respawn) doesn't flap the probe.
_HEALTH_STALE_SECONDS = 30.0
_shutdown = False
_child_shutdown_requested = False
_CHILD_SHUTDOWN_GRACE_S = 0.2
_CHILD_KILL_JOIN_S = 0.5


@dataclass
class _RestartTracker:
    attempt: int = 0
    started_at: float = 0.0
    # Wall-clock (monotonic) instant when the next respawn becomes eligible.
    # 0 means "no pending restart". Tracked per-process so backoff for one
    # crashed worker doesn't stall detection / restart of others.
    restart_at: float = 0.0


def _cancel_child_asyncio_tasks() -> bool:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    for task in asyncio.all_tasks(loop):
        if not task.done():
            task.cancel()
    return True


def _install_child_signal_handlers() -> None:
    """Children ignore SIGINT (parent's SIGTERM drives shutdown).

    Ctrl+C in a terminal delivers SIGINT to the whole process group, so
    children would otherwise raise ``KeyboardInterrupt`` mid-``asyncio.run``
    in parallel with the parent's graceful path — producing the noisy
    OTel/multiprocessing atexit tracebacks. Ignoring SIGINT here makes the
    parent the single source of truth: it catches the Ctrl+C, marks
    ``_shutdown``, and ``proc.terminate()`` (SIGTERM) walks each child out.
    """
    global _child_shutdown_requested

    _child_shutdown_requested = False
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def _term(_signum: int, _frame: object) -> None:
        global _child_shutdown_requested

        if _child_shutdown_requested:
            raise KeyboardInterrupt

        _child_shutdown_requested = True
        # Ask the active asyncio workload to unwind cooperatively. Raising
        # KeyboardInterrupt here can land inside whichever callback is running
        # (including background analysis tasks), which produces noisy
        # "Task exception was never retrieved" shutdown tracebacks.
        if _cancel_child_asyncio_tasks():
            return

        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)


def _install_orphan_watchdog(poll_s: float = 2.0) -> None:
    """Graceful self-shutdown when our spawner dies.

    Workers run detached (``start_new_session`` for the isolated instance runner)
    or as ``multiprocessing`` children, so a dead parent reparents us to init
    (PID 1) instead of taking us down — the process would otherwise linger and
    keep logging/heartbeating forever. Watch for that reparent and SIGTERM
    ourselves: the handler installed above unwinds asyncio cleanly and releases
    the device/scrcpy session.

    Triggers on "``getppid()`` changed" rather than "== 1", so a process started
    already orphaned (container/launchd) has no owner to track and is left alone.
    """
    initial_ppid = os.getppid()
    if initial_ppid <= 1:
        return  # launched already orphaned — no owning parent to track

    def _watch() -> None:
        while True:
            time.sleep(poll_s)
            if os.getppid() != initial_ppid:  # original parent gone → reparented
                logger.info(
                    "orphan watchdog: parent %s gone, shutting down", initial_ppid
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=_watch, daemon=True, name="orphan-watchdog").start()


def _worker_process(instance_config: InstanceConfig) -> None:
    _install_child_signal_handlers()
    _install_orphan_watchdog()
    bootstrap_runtime_observability("worker", instance_id=instance_config.instance_id)

    async def _run() -> None:
        from dataclasses import replace

        from services import (
            aclose_app_services,
            bind_active_game,
            init_app_services,
            instance_worker_session,
            resolve_effective_game,
        )

        # Load Settings first — ``resolve_effective_game`` probes the device and
        # reads the resolved adb path / instance list from Settings.
        await init_app_services()
        # Adopt the game actually running on the device when it differs from the
        # configured one, so the worker doesn't force-stop a live game (e.g. WOS)
        # and launch the wrong one (the configured game).
        effective_game = resolve_effective_game(instance_config)
        cfg = (
            instance_config
            if effective_game == instance_config.game
            else replace(instance_config, game=effective_game)
        )
        bind_active_game(cfg.game)
        try:
            async with instance_worker_session(cfg) as worker:
                await worker.run()
        finally:
            await aclose_app_services()

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Flush OTel exporters here so the interpreter's atexit doesn't have
        # to — atexit runs after signal handlers are restored to default,
        # and a follow-up Ctrl+C would re-interrupt MeterProvider.shutdown.
        shutdown_runtime_observability()


def _scheduler_process() -> None:
    _install_child_signal_handlers()
    _install_orphan_watchdog()
    bootstrap_runtime_observability("scheduler")

    async def _run() -> None:
        from services import (
            aclose_app_services,
            get_scheduler_runner,
            init_app_services,
        )

        await init_app_services()
        try:
            runner = await get_scheduler_runner()
            await runner.run()
        finally:
            await aclose_app_services()

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        shutdown_runtime_observability()


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    global _shutdown
    name = signal.Signals(signum).name if signum in signal.Signals.__members__.values() else str(signum)
    if _shutdown:
        # Second Ctrl+C — give up on graceful path and let the default
        # handler do its job (raises KeyboardInterrupt at the next opcode).
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        logger.warning("%s received again — forcing exit", name)
        return
    logger.info("%s received — initiating graceful shutdown", name)
    _shutdown = True


class Supervisor:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._processes: dict[str, multiprocessing.Process] = {}
        self._restart: dict[str, _RestartTracker] = {}
        self._device_reconcile_client: Any | None = None
        self._device_reconcile_pubsub: Any | None = None
        # Monotonic instant of the last reconcile-loop tick; backs ``is_healthy``
        # / the ``/health`` endpoint. Seeded to "now" so the probe is green from
        # construction (before ``run()`` takes its first tick).
        self._last_tick = time.monotonic()

    def is_healthy(self) -> bool:
        """True while the reconcile loop is ticking (not shut down / not hung)."""
        if _shutdown:
            return False
        return (time.monotonic() - self._last_tick) < _HEALTH_STALE_SECONDS

    def _spawn_worker(self, instance_config: InstanceConfig) -> multiprocessing.Process:
        proc = multiprocessing.Process(
            target=_worker_process,
            args=(instance_config,),
            name=f"worker-{instance_config.instance_id}",
            daemon=False,
        )
        proc.start()
        self._restart.setdefault(instance_config.instance_id, _RestartTracker()).started_at = time.monotonic()
        logger.info(
            "Spawned worker for instance %s (pid=%d)",
            instance_config.instance_id,
            proc.pid,
        )
        return proc

    def _spawn_scheduler(self) -> multiprocessing.Process:
        proc = multiprocessing.Process(
            target=_scheduler_process,
            name="scheduler",
            daemon=False,
        )
        proc.start()
        self._restart.setdefault("scheduler", _RestartTracker()).started_at = time.monotonic()
        logger.info("Spawned scheduler (pid=%d)", proc.pid)
        return proc

    @staticmethod
    def _instance_signature(instance_config: InstanceConfig) -> tuple[str, ...]:
        return (
            instance_config.instance_id,
            instance_config.bluestacks_window_title,
            instance_config.screenshot_backend,
            instance_config.input_backend,
            instance_config.game,
            repr(instance_config.display),
        )

    def _stop_worker_process(self, name: str, *, reason: str) -> None:
        proc = self._processes.pop(name, None)
        self._restart.pop(name, None)
        if proc is None:
            return
        if proc.is_alive():
            logger.info("Stopping worker for instance %s (%s)", name, reason)
            with _suppress(ProcessLookupError, OSError):
                proc.terminate()
            proc.join(timeout=_CHILD_SHUTDOWN_GRACE_S)
            if proc.is_alive():
                logger.warning("Process %s did not exit cleanly, killing", name)
                proc.kill()
                proc.join(timeout=_CHILD_KILL_JOIN_S)
        else:
            proc.join(timeout=0)

    def _read_fresh_settings(self) -> Any:
        from config.devices import invalidate_device_registry

        invalidate_device_registry()
        return load_settings()

    def _refresh_settings_snapshot(self) -> None:
        fresh = self._read_fresh_settings()
        self._settings = fresh
        set_settings(fresh)

    def _ensure_device_reconcile_subscription(self) -> bool:
        if self._device_reconcile_pubsub is not None:
            return True
        try:
            import redis

            from dashboard.dashboard_events import DEVICE_RECONCILE_CHANNEL

            client = redis.Redis.from_url(
                self._settings.redis.url,
                socket_connect_timeout=5.0,
                decode_responses=True,
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(DEVICE_RECONCILE_CHANNEL)
        except Exception:
            logger.debug("Device reconcile: failed to subscribe", exc_info=True)
            return False
        self._device_reconcile_client = client
        self._device_reconcile_pubsub = pubsub
        return True

    def _close_device_reconcile_subscription(self) -> None:
        pubsub = self._device_reconcile_pubsub
        client = self._device_reconcile_client
        self._device_reconcile_pubsub = None
        self._device_reconcile_client = None
        if pubsub is not None:
            with _suppress(Exception):
                pubsub.close()
        if client is not None:
            with _suppress(Exception):
                client.close()

    def _device_reconcile_requested(self) -> bool:
        requested = False
        if self._ensure_device_reconcile_subscription():
            assert self._device_reconcile_pubsub is not None
            try:
                while True:
                    msg = self._device_reconcile_pubsub.get_message(timeout=0)
                    if msg is None:
                        break
                    if msg.get("type") == "message":
                        requested = True
            except Exception:
                logger.debug("Device reconcile: pubsub read failed", exc_info=True)
                self._close_device_reconcile_subscription()

        return requested

    def _reconcile_devices(self) -> None:
        """Hot-add/remove workers when the SQLite device registry changes.

        The embedded async supervisor already has Redis-driven reconcile. The
        production Docker entrypoint runs this multiprocess supervisor instead,
        so it listens to the same device-reconcile event and reloads when the
        UI/API has saved a registry mutation.
        """
        try:
            fresh = self._read_fresh_settings()
        except Exception:
            logger.warning("Device reconcile: failed to reload settings", exc_info=True)
            return

        current = {inst.instance_id: inst for inst in self._settings.instances}
        desired = {inst.instance_id: inst for inst in fresh.instances}
        current_signatures = {
            iid: self._instance_signature(inst) for iid, inst in current.items()
        }
        desired_signatures = {
            iid: self._instance_signature(inst) for iid, inst in desired.items()
        }

        removed = sorted(set(current) - set(desired))
        added = sorted(set(desired) - set(current))
        changed = sorted(
            iid
            for iid in set(current) & set(desired)
            if current_signatures[iid] != desired_signatures[iid]
        )
        if not (added or removed or changed):
            return

        logger.info(
            "Device registry changed (added=%s removed=%s changed=%s)",
            added or "-",
            removed or "-",
            changed or "-",
        )
        self._settings = fresh
        set_settings(fresh)

        for iid in [*removed, *changed]:
            self._stop_worker_process(
                iid,
                reason="unregistered" if iid in removed else "configuration changed",
            )

        if added or changed:
            self._stamp_worker_started_at([desired[iid] for iid in [*added, *changed]])
        for iid in [*added, *changed]:
            self._processes[iid] = self._spawn_worker(desired[iid])

    def _restart_delay_for(self, name: str) -> float:
        tracker = self._restart.setdefault(name, _RestartTracker())
        ran_for = time.monotonic() - tracker.started_at if tracker.started_at else 0.0
        if ran_for > _STABILITY_WINDOW_SECONDS:
            tracker.attempt = 1  # stabilized — reset backoff
        else:
            tracker.attempt += 1
        return compute_restart_delay(
            tracker.attempt, base_seconds=_BASE_RESTART_DELAY_SECONDS
        )

    def run(self) -> None:
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
        signal.signal(signal.SIGINT, _handle_shutdown_signal)

        # Subscribe before the initial registry read so a UI save that happens
        # during startup is either included in this snapshot or queued as an
        # explicit reconcile event.
        self._ensure_device_reconcile_subscription()
        self._refresh_settings_snapshot()

        # Stamp a fresh ``worker_started_at`` per instance before any worker
        # boots. This is the only place the field is written on the happy
        # path — workers reconnect via ``hsetnx`` so a crash-restart wave
        # preserves this session's start time instead of jumping to "now".
        self._stamp_worker_started_at(self._settings.instances)

        for instance in self._settings.instances:
            self._processes[instance.instance_id] = self._spawn_worker(instance)

        self._processes["scheduler"] = self._spawn_scheduler()

        while not _shutdown:
            now = time.monotonic()
            self._last_tick = now  # heartbeat for the /health endpoint
            if self._device_reconcile_requested():
                self._reconcile_devices()
            for name, proc in list(self._processes.items()):
                if proc.is_alive():
                    continue
                # ``proc.is_alive()`` on POSIX polls via ``waitpid(WNOHANG)``
                # which already reaps the child, so no explicit join() is
                # needed here for zombie collection.
                tracker = self._restart.setdefault(name, _RestartTracker())
                if tracker.restart_at == 0.0:
                    delay = self._restart_delay_for(name)
                    tracker.restart_at = now + delay
                    logger.warning(
                        "Process %s (pid=%s) died (attempt=%d) — restart in %.1fs",
                        name,
                        proc.pid,
                        tracker.attempt,
                        delay,
                    )
                    continue
                if now < tracker.restart_at:
                    # Still in backoff window — keep checking other processes.
                    continue
                tracker.restart_at = 0.0
                # Increment the restart counter *before* re-spawning so the
                # metric reflects the decision to restart even if the spawn
                # itself fails. ``attempt`` is the count for this restart wave.
                telemetry.report_restart(name, attempt=tracker.attempt)
                if name == "scheduler":
                    self._processes["scheduler"] = self._spawn_scheduler()
                else:
                    instance = self._find_instance(name)
                    if instance:
                        self._processes[name] = self._spawn_worker(instance)
            # ``time.sleep`` raises if a signal handler raised — wrap so a
            # late SIGINT during shutdown just breaks the loop cleanly
            # instead of propagating into the join path below.
            try:
                time.sleep(1.0)
            except (InterruptedError, KeyboardInterrupt):
                break

        logger.info("Supervisor shutting down — killing workers")
        self._close_device_reconcile_subscription()
        # Children ignore SIGINT; signal them explicitly so they begin
        # tearing down in parallel rather than waiting for the 30s join.
        for proc in self._processes.values():
            if proc.is_alive():
                with _suppress(ProcessLookupError, OSError):
                    proc.terminate()
        for name, proc in self._processes.items():
            proc.join(timeout=_CHILD_SHUTDOWN_GRACE_S)
            if proc.is_alive():
                logger.warning("Process %s did not exit cleanly, killing", name)
                proc.kill()
                proc.join(timeout=_CHILD_KILL_JOIN_S)

    def _find_instance(self, instance_id: str) -> InstanceConfig | None:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst
        return None

    def _stamp_worker_started_at(self, instances: list[InstanceConfig]) -> None:
        """One-shot Redis write at supervisor boot: anchors uptime to the
        supervisor lifecycle, not the worker subprocess lifecycle.

        Failures are non-fatal — the worker's ``hsetnx`` fallback covers
        a transient Redis outage here, and the only consequence of a miss
        is one freshly-restarted instance briefly showing 0s uptime.
        """
        if not instances:
            return
        try:
            import redis

            client = redis.Redis.from_url(
                self._settings.redis.url, socket_connect_timeout=5.0
            )
        except Exception:
            logger.warning(
                "Could not connect to Redis to stamp worker_started_at — "
                "uptime will fall back to per-subprocess time",
                exc_info=True,
            )
            return
        now = str(time.time())
        try:
            pipe = client.pipeline(transaction=False)
            for instance in instances:
                key = f"wos:instance:{instance.instance_id}:state"
                pipe.hset(key, "worker_started_at", now)
            pipe.execute()
        except Exception:
            logger.warning("Failed to stamp worker_started_at", exc_info=True)
        finally:
            with _suppress(Exception):
                client.close()


def main() -> None:
    # Stamp ``service.instance.id`` with a stable id. ``WOS_INSTANCE_ID`` lets
    # container deployments pin one (Docker hostnames are container IDs that
    # churn on every recreate, spawning a fresh Prometheus series each restart);
    # otherwise fall back to the host name.
    bootstrap_runtime_observability(
        "supervisor",
        instance_id=os.environ.get("WOS_INSTANCE_ID") or socket.gethostname(),
    )
    settings = load_settings()
    set_settings(settings)
    # Host-networked containers share the host's loopback, so an adb server
    # started here listens on 127.0.0.1:5037 for the whole host — the user never
    # needs adb installed or ``adb start-server`` run on the host. Idempotent and
    # non-fatal everywhere (no-op when a server is already up, e.g. local dev).
    from adb import ensure_adb_server

    ensure_adb_server(settings.worker.adb_executable or "adb")
    assert_startup_configs_valid()
    cleanup_orphaned_sck_capture_helpers()
    ensure_health_watchdog_process()
    multiprocessing.set_start_method("spawn", force=True)
    supervisor = Supervisor()
    # The ``autopilot.workers.active`` gauge needs to peek at the supervisor's
    # process table — bind it here so the callback finds it.
    telemetry.bind_supervisor(supervisor)

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    health_server = start_health_server(supervisor.is_healthy, port=_health_port())
    try:
        supervisor.run()
    finally:
        if health_server is not None:
            health_server.shutdown()
        stop_health_watchdog_process()
        shutdown_runtime_observability()


def _health_port() -> int:
    raw = os.environ.get("WOS_BOT_HEALTH_PORT", "").strip()
    if not raw:
        return _DEFAULT_HEALTH_PORT
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid WOS_BOT_HEALTH_PORT=%r — using %d", raw, _DEFAULT_HEALTH_PORT)
        return _DEFAULT_HEALTH_PORT


if __name__ == "__main__":
    main()
