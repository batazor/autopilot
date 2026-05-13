from __future__ import annotations

import asyncio
import logging
import multiprocessing
import signal
import time
from dataclasses import dataclass

from config.loader import InstanceConfig, get_settings
from config.logging_stdout import setup_stdout_logging
from config.startup_validation import assert_startup_configs_valid
from scheduler.runner import main as scheduler_main
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


def _worker_process(instance_config: InstanceConfig) -> None:
    setup_stdout_logging()
    from worker.instance_worker import InstanceWorker

    worker = InstanceWorker(instance_config)
    asyncio.run(worker.run())


def _scheduler_process() -> None:
    scheduler_main()


def _handle_sigterm(signum: int, frame: object) -> None:
    global _shutdown  # noqa: PLW0603
    logger.info("SIGTERM received — initiating graceful shutdown")
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
        signal.signal(signal.SIGTERM, _handle_sigterm)

        for instance in self._settings.instances:
            self._processes[instance.instance_id] = self._spawn_worker(instance)

        self._processes["scheduler"] = self._spawn_scheduler()

        while not _shutdown:
            for name, proc in list(self._processes.items()):
                if not proc.is_alive():
                    delay = self._restart_delay_for(name)
                    attempt = self._restart[name].attempt
                    logger.warning(
                        "Process %s (pid=%s) died (attempt=%d) — restart in %.1fs",
                        name,
                        proc.pid,
                        attempt,
                        delay,
                    )
                    time.sleep(delay)
                    if name == "scheduler":
                        self._processes["scheduler"] = self._spawn_scheduler()
                    else:
                        instance = self._find_instance(name)
                        if instance:
                            self._processes[name] = self._spawn_worker(instance)
            time.sleep(5.0)

        logger.info("Supervisor shutting down — waiting for workers to finish")
        for name, proc in self._processes.items():
            proc.join(timeout=30)
            if proc.is_alive():
                logger.warning("Process %s did not exit cleanly, terminating", name)
                proc.terminate()

    def _find_instance(self, instance_id: str) -> InstanceConfig | None:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst
        return None


def main() -> None:
    setup_stdout_logging()
    assert_startup_configs_valid()
    multiprocessing.set_start_method("spawn", force=True)
    supervisor = Supervisor()
    supervisor.run()


if __name__ == "__main__":
    main()
