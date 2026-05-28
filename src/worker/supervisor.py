from __future__ import annotations

import asyncio
import logging
import multiprocessing
import signal
import time
from contextlib import suppress as _suppress
from dataclasses import dataclass

from config import telemetry
from config.loader import InstanceConfig, get_settings, load_settings, set_settings
from config.runtime_bootstrap import (
    bootstrap_runtime_observability,
    shutdown_runtime_observability,
)
from config.startup_validation import assert_startup_configs_valid
from licensing import LicenseError, generate_fingerprint, load_license
from worker.restart_backoff import compute_restart_delay

logger = logging.getLogger(__name__)

# Base delay; exponential backoff + jitter applied on top via
# ``compute_restart_delay``. Matches the embedded supervisor's behavior so
# operators see consistent restart timings between deployments.
_BASE_RESTART_DELAY_SECONDS = 10.0
# A child process that ran for longer than ``base * _STABILITY_FACTOR`` is
# treated as stabilized — the next failure resets its backoff counter.
_STABILITY_FACTOR = 4
_shutdown = False


@dataclass
class _RestartTracker:
    attempt: int = 0
    started_at: float = 0.0
    # Wall-clock (monotonic) instant when the next respawn becomes eligible.
    # 0 means "no pending restart". Tracked per-process so backoff for one
    # crashed worker doesn't stall detection / restart of others.
    restart_at: float = 0.0


def _install_child_signal_handlers() -> None:
    """Children ignore SIGINT (parent's SIGTERM drives shutdown).

    Ctrl+C in a terminal delivers SIGINT to the whole process group, so
    children would otherwise raise ``KeyboardInterrupt`` mid-``asyncio.run``
    in parallel with the parent's graceful path — producing the noisy
    OTel/multiprocessing atexit tracebacks. Ignoring SIGINT here makes the
    parent the single source of truth: it catches the Ctrl+C, marks
    ``_shutdown``, and ``proc.terminate()`` (SIGTERM) walks each child out.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def _term(_signum: int, _frame: object) -> None:
        # Convert SIGTERM into a KeyboardInterrupt so ``asyncio.run`` unwinds
        # through the ``finally`` block in ``_run`` (closing Redis, OCR, etc.)
        # instead of dying at an arbitrary await point.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)


def _worker_process(instance_config: InstanceConfig) -> None:
    _install_child_signal_handlers()
    bootstrap_runtime_observability("worker", instance_id=instance_config.instance_id)

    async def _run() -> None:
        from services import (
            aclose_app_services,
            bind_active_game,
            init_app_services,
            instance_worker_session,
        )

        bind_active_game(instance_config.game)
        await init_app_services()
        try:
            async with instance_worker_session(instance_config) as worker:
                await worker.run()
        finally:
            await aclose_app_services()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        # Flush OTel exporters here so the interpreter's atexit doesn't have
        # to — atexit runs after signal handlers are restored to default,
        # and a follow-up Ctrl+C would re-interrupt MeterProvider.shutdown.
        shutdown_runtime_observability()


def _scheduler_process() -> None:
    _install_child_signal_handlers()
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
    except KeyboardInterrupt:
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

    def _restart_delay_for(self, name: str) -> float:
        tracker = self._restart.setdefault(name, _RestartTracker())
        ran_for = time.monotonic() - tracker.started_at if tracker.started_at else 0.0
        if ran_for > _BASE_RESTART_DELAY_SECONDS * _STABILITY_FACTOR:
            tracker.attempt = 1  # stabilized — reset backoff
        else:
            tracker.attempt += 1
        return compute_restart_delay(
            tracker.attempt, base_seconds=_BASE_RESTART_DELAY_SECONDS
        )

    def run(self) -> None:
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
        signal.signal(signal.SIGINT, _handle_shutdown_signal)

        # Stamp a fresh ``worker_started_at`` per instance before any worker
        # boots. This is the only place the field is written on the happy
        # path — workers reconnect via ``hsetnx`` so a crash-restart wave
        # preserves this session's start time instead of jumping to "now".
        self._stamp_worker_started_at()

        for instance in self._settings.instances:
            self._processes[instance.instance_id] = self._spawn_worker(instance)

        self._processes["scheduler"] = self._spawn_scheduler()

        while not _shutdown:
            now = time.monotonic()
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

        logger.info("Supervisor shutting down — waiting for workers to finish")
        # Children ignore SIGINT; signal them explicitly so they begin
        # tearing down in parallel rather than waiting for the 30s join.
        for proc in self._processes.values():
            if proc.is_alive():
                with _suppress(ProcessLookupError, OSError):
                    proc.terminate()
        for name, proc in self._processes.items():
            proc.join(timeout=30)
            if proc.is_alive():
                logger.warning("Process %s did not exit cleanly, killing", name)
                proc.kill()
                proc.join(timeout=5)

    def _find_instance(self, instance_id: str) -> InstanceConfig | None:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst
        return None

    def _stamp_worker_started_at(self) -> None:
        """One-shot Redis write at supervisor boot: anchors uptime to the
        supervisor lifecycle, not the worker subprocess lifecycle.

        Failures are non-fatal — the worker's ``hsetnx`` fallback covers
        a transient Redis outage here, and the only consequence of a miss
        is one freshly-restarted instance briefly showing 0s uptime.
        """
        if not self._settings.instances:
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
            for instance in self._settings.instances:
                key = f"wos:instance:{instance.instance_id}:state"
                pipe.hset(key, "worker_started_at", now)
            pipe.execute()
        except Exception:
            logger.warning("Failed to stamp worker_started_at", exc_info=True)
        finally:
            with _suppress(Exception):
                client.close()


def _enforce_license_gate() -> None:
    """Refuse to start workers without a valid license bound to this host.

    Runs before any subprocess is spawned so a missing license fails fast
    with a single clear error instead of cascading through child crashes.
    """
    fingerprint = generate_fingerprint()
    try:
        claims = load_license()
    except LicenseError as exc:
        # Not using ``logger.exception`` here — the LicenseError's full
        # context is captured in ``exc.reason`` and the fingerprint hint
        # below. A stack trace would just be noise for users on the
        # config-level refusal path.
        logger.error(  # noqa: TRY400
            "license gate: refusing to start (%s). "
            "Drop a license file via the UI (/license) or set WOS_LICENSE. "
            "This host's fingerprint is: %s",
            exc.reason,
            fingerprint,
        )
        # Telemetry still works here — the meter provider is up regardless of
        # license state, and this counter is the only signal we get for
        # "user tried to start without a valid license". Stash the fingerprint
        # so the counter's label has *something* even though no LicenseClaims
        # exist yet.
        telemetry._state["host_fingerprint"] = fingerprint
        telemetry.report_license_gate_failure(exc.code)
        raise SystemExit(78) from exc  # EX_CONFIG — config-level refusal
    days_left = claims.days_until_expiry()
    if days_left is not None and days_left < 7:
        logger.warning(
            "license expires in %.1f day(s) (sub=%s, tier=%s) — request a renewal",
            days_left, claims.sub, claims.tier,
        )
    else:
        logger.info(
            "license OK (sub=%s, tier=%s, expires_in=%.1fd)",
            claims.sub, claims.tier, days_left if days_left is not None else float("inf"),
        )
    # Bind the validated claims so heartbeat / uptime / workers_active
    # observations carry user-identifying attributes.
    telemetry.bind_license_claims(claims, host_fingerprint=fingerprint)


def main() -> None:
    # Stamp ``service.instance.id`` with the host fingerprint instead of the
    # hostname (Docker container IDs churn on every recreate — each restart
    # would spawn a fresh time series in Prometheus until the old ones aged
    # out). Fingerprint is stable per host: same machine = same id.
    bootstrap_runtime_observability("supervisor", instance_id=generate_fingerprint())
    set_settings(load_settings())
    assert_startup_configs_valid()
    _enforce_license_gate()
    multiprocessing.set_start_method("spawn", force=True)
    supervisor = Supervisor()
    # The ``autopilot.workers.active`` gauge needs to peek at the supervisor's
    # process table — bind it here so the callback finds it.
    telemetry.bind_supervisor(supervisor)
    try:
        supervisor.run()
    finally:
        shutdown_runtime_observability()


if __name__ == "__main__":
    main()
