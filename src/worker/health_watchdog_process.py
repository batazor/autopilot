"""Lifecycle helper for the independent game-health watchdog process."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from config.paths import repo_root

_HEALTH_WATCHDOG_MODULE = "worker.game_health_watchdog"
# Launch by importing the module's ``main()`` via ``-c``. The module path still
# appears verbatim in argv, so ``is_health_watchdog_process`` can match it.
_HEALTH_WATCHDOG_LAUNCH_CODE = f"from {_HEALTH_WATCHDOG_MODULE} import main; main()"

_lock = threading.RLock()
_health_proc: subprocess.Popen[bytes] | None = None
_known_health_watchdog_pid: int | None = None


def is_health_watchdog_process(proc: psutil.Process, repo: Path) -> bool:
    try:
        if proc.pid == os.getpid():
            return False
        cmdline = proc.cmdline()
        # Match both the current ``-c "from <module> import main; main()"`` launch
        # and the legacy ``-m <module>`` form (older processes mid-rollout): the
        # module path appears verbatim in some argv token either way.
        if not any(_HEALTH_WATCHDOG_MODULE in arg for arg in cmdline):
            return False
        return Path(proc.cwd()).resolve() == repo
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def health_watchdog_processes(repo: Path | None = None) -> list[psutil.Process]:
    root = repo or repo_root()
    return [
        proc
        for proc in psutil.process_iter()
        if is_health_watchdog_process(proc, root)
    ]


def existing_health_watchdog_process(repo: Path | None = None) -> psutil.Process | None:
    for proc in health_watchdog_processes(repo):
        return proc
    return None


def ensure_health_watchdog_process(*, log: logging.Logger | None = None) -> None:
    """Spawn the ``worker.game_health_watchdog`` subprocess if not already running."""
    global _health_proc, _known_health_watchdog_pid
    logger = log or logging.getLogger(__name__)
    with _lock:
        if _health_proc is not None and _health_proc.poll() is None:
            _known_health_watchdog_pid = _health_proc.pid
            return
        _health_proc = None
        repo = repo_root()
        existing = existing_health_watchdog_process(repo)
        if existing is not None:
            if _known_health_watchdog_pid != existing.pid:
                logger.info(
                    "Game health watchdog subprocess already running pid=%s",
                    existing.pid,
                )
            else:
                logger.debug(
                    "Game health watchdog subprocess already running pid=%s",
                    existing.pid,
                )
            _known_health_watchdog_pid = existing.pid
            return
        try:
            _health_proc = subprocess.Popen(
                [sys.executable, "-c", _HEALTH_WATCHDOG_LAUNCH_CODE],
                cwd=str(repo),
                env=os.environ.copy(),
            )
            _known_health_watchdog_pid = _health_proc.pid
            logger.info("Game health watchdog subprocess pid=%s", _health_proc.pid)
        except Exception:
            logger.exception("Failed to start game health watchdog subprocess")


def stop_health_watchdog_process(*, log: logging.Logger | None = None) -> None:
    """Terminate the managed watchdog and any repo-local orphan watchdogs."""
    del log
    global _health_proc, _known_health_watchdog_pid
    repo = repo_root()
    with _lock:
        proc = _health_proc
        _health_proc = None
        _known_health_watchdog_pid = None
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    for existing in health_watchdog_processes(repo):
        existing.terminate()
        try:
            existing.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            existing.kill()
