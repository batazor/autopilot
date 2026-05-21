"""Local dev bot lifecycle: detect / start / stop worker + health watchdog.

Used by ``uv run play`` (optional subprocess supervisor) and the dashboard API
(embedded async supervisor in the API process).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

import psutil

from config.paths import repo_root

_SUPERVISOR_MODULE = "worker.supervisor"
_EMBEDDED_THREAD_NAME = "wos-async-services"
BotMode = Literal["supervisor", "embedded"] | None


def _is_repo_supervisor_process(proc: psutil.Process, repo: os.PathLike[str]) -> bool:
    try:
        if proc.pid == os.getpid():
            return False
        cmdline = proc.cmdline()
        if not any(
            arg == "-m"
            and idx + 1 < len(cmdline)
            and cmdline[idx + 1] == _SUPERVISOR_MODULE
            for idx, arg in enumerate(cmdline)
        ):
            return False
        return Path(proc.cwd()).resolve() == Path(repo).resolve()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _supervisor_processes(repo: os.PathLike[str] | None = None) -> list[psutil.Process]:
    root = Path(repo or repo_root())
    return [
        proc
        for proc in psutil.process_iter()
        if _is_repo_supervisor_process(proc, root)
    ]


def _embedded_thread_alive() -> bool:
    import threading

    return any(
        t.name == _EMBEDDED_THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def bot_status() -> dict[str, Any]:
    """Return ``{running, mode, pid}`` for the local worker stack."""
    sup = _supervisor_processes()
    if sup:
        proc = sup[0]
        return {"running": True, "mode": "supervisor", "pid": proc.pid}
    if _embedded_thread_alive():
        return {"running": True, "mode": "embedded", "pid": None}
    return {"running": False, "mode": None, "pid": None}


def start_supervisor_subprocess() -> dict[str, Any]:
    """Spawn ``python -m worker.supervisor`` plus the health watchdog."""
    if bot_status()["running"]:
        return bot_status()
    from dashboard.bot_services import ensure_health_watchdog

    repo = repo_root()
    kwargs: dict[str, object] = {
        "cwd": str(repo),
        "env": os.environ.copy(),
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", _SUPERVISOR_MODULE],
        **kwargs,  # type: ignore[arg-type]
    )
    ensure_health_watchdog()
    return {"running": True, "mode": "supervisor", "pid": proc.pid}


def start_embedded_bot() -> dict[str, Any]:
    """Start the async supervisor thread in the current process."""
    if bot_status()["running"]:
        return bot_status()
    from dashboard.bot_services import ensure_embedded_bot

    ensure_embedded_bot()
    return bot_status()


def stop_supervisor_subprocess() -> dict[str, Any]:
    """Terminate repo-local ``worker.supervisor`` processes."""
    for proc in _supervisor_processes():
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            proc.kill()
    return bot_status()


def stop_embedded_bot(*, join_timeout_s: float = 5.0) -> dict[str, Any]:
    from dashboard.bot_services import stop_embedded_bot as _stop

    _stop(join_timeout_s=join_timeout_s)
    return bot_status()


def stop_local_bot(*, join_timeout_s: float = 5.0) -> dict[str, Any]:
    """Stop whichever local bot mode is active."""
    status = bot_status()
    if not status["running"]:
        return status
    if status["mode"] == "supervisor":
        return stop_supervisor_subprocess()
    return stop_embedded_bot(join_timeout_s=join_timeout_s)
