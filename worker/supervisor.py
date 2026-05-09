from __future__ import annotations

import asyncio
import logging
import multiprocessing
import signal
import time

from config.loader import InstanceConfig, get_settings
from config.logging_stdout import setup_stdout_logging
from config.startup_validation import assert_startup_configs_valid
from scheduler.runner import main as scheduler_main

logger = logging.getLogger(__name__)

_RESTART_DELAY_SECONDS = 10
_shutdown = False


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

    def _spawn_worker(self, instance_config: InstanceConfig) -> multiprocessing.Process:
        proc = multiprocessing.Process(
            target=_worker_process,
            args=(instance_config,),
            name=f"worker-{instance_config.instance_id}",
            daemon=False,
        )
        proc.start()
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
        logger.info("Spawned scheduler (pid=%d)", proc.pid)
        return proc

    def run(self) -> None:
        signal.signal(signal.SIGTERM, _handle_sigterm)

        for instance in self._settings.instances:
            self._processes[instance.instance_id] = self._spawn_worker(instance)

        self._processes["scheduler"] = self._spawn_scheduler()

        while not _shutdown:
            for name, proc in list(self._processes.items()):
                if not proc.is_alive():
                    logger.warning("Process %s (pid=%s) died — restarting in %ds",
                                   name, proc.pid, _RESTART_DELAY_SECONDS)
                    time.sleep(_RESTART_DELAY_SECONDS)
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
