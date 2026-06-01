"""Local dev bot lifecycle: detect / start / stop worker + health watchdog.

Used by ``uv run play`` (optional subprocess supervisor) and the dashboard API
(embedded async supervisor in the API process).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

import psutil

from config.paths import repo_root

_SUPERVISOR_MODULE = "worker.supervisor"
_EMBEDDED_THREAD_NAME = "wos-async-services"
BotMode = Literal["supervisor", "embedded"] | None

# (pid, create_time) -> match verdict. cmdline/cwd are immutable for a live
# process, so once we've classified a PID we can answer subsequent polls for
# free. create_time disambiguates PID reuse; OrderedDict caps memory growth.
_PROCESS_VERDICT_CACHE: OrderedDict[tuple[int, float], bool] = OrderedDict()
_PROCESS_VERDICT_CACHE_MAX = 4096


def _is_repo_supervisor_process(proc: psutil.Process, repo: os.PathLike[str]) -> bool:
    try:
        if proc.pid == os.getpid():
            return False
        with proc.oneshot():
            create_time = proc.create_time()
            key = (int(proc.pid), float(create_time))
            cached = _PROCESS_VERDICT_CACHE.get(key)
            if cached is not None:
                _PROCESS_VERDICT_CACHE.move_to_end(key)
                return cached
            cmdline = proc.cmdline()
            matches_module = any(
                arg == "-m"
                and idx + 1 < len(cmdline)
                and cmdline[idx + 1] == _SUPERVISOR_MODULE
                for idx, arg in enumerate(cmdline)
            )
            verdict = matches_module and Path(proc.cwd()).resolve() == Path(repo).resolve()
        _PROCESS_VERDICT_CACHE[key] = verdict
        _PROCESS_VERDICT_CACHE.move_to_end(key)
        while len(_PROCESS_VERDICT_CACHE) > _PROCESS_VERDICT_CACHE_MAX:
            _PROCESS_VERDICT_CACHE.popitem(last=False)
        return verdict
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


def _supervisor_process_summary(proc: psutil.Process) -> dict[str, Any]:
    """Lightweight {pid, started_at} snapshot for the UI carousel."""
    try:
        started_at: float | None = float(proc.create_time())
    except (psutil.Error, OSError):
        started_at = None
    return {"pid": proc.pid, "started_at": started_at}


def bot_status() -> dict[str, Any]:
    """Return ``{running, mode, pid, processes}`` for the local worker stack.

    ``processes`` is the full list of detected supervisors (typically 1, but
    dev cycles or accidental double-starts can leave several behind). The
    legacy ``pid`` field stays for back-compat and points at the first one.
    """
    sup = _supervisor_processes()
    if sup:
        processes = [_supervisor_process_summary(p) for p in sup]
        # Stable order: oldest first. ``started_at == None`` sorts last so a
        # process whose create_time was unreadable doesn't shuffle the rest.
        processes.sort(key=lambda p: (p["started_at"] is None, p["started_at"] or 0.0))
        return {
            "running": True,
            "mode": "supervisor",
            "pid": processes[0]["pid"],
            "processes": processes,
        }
    if _embedded_thread_alive():
        return {
            "running": True,
            "mode": "embedded",
            "pid": None,
            "processes": [{"pid": None, "started_at": None}],
        }
    return {"running": False, "mode": None, "pid": None, "processes": []}


def start_supervisor_subprocess() -> dict[str, Any]:
    """Spawn ``python -m worker.supervisor`` plus the health watchdog."""
    status = bot_status()
    if status["running"]:
        from worker.health_watchdog_process import ensure_health_watchdog_process

        ensure_health_watchdog_process()
        return status
    from worker.health_watchdog_process import ensure_health_watchdog_process

    repo = repo_root()
    ensure_health_watchdog_process()
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
    return {"running": True, "mode": "supervisor", "pid": proc.pid}


def start_embedded_bot() -> dict[str, Any]:
    """Start the async supervisor thread in the current process."""
    status = bot_status()
    if status["running"]:
        from worker.health_watchdog_process import ensure_health_watchdog_process

        ensure_health_watchdog_process()
        return status
    from dashboard.bot_services import ensure_embedded_bot

    ensure_embedded_bot()
    return bot_status()


def stop_supervisor_subprocess() -> dict[str, Any]:
    """Terminate repo-local ``worker.supervisor`` processes."""
    from worker.health_watchdog_process import stop_health_watchdog_process

    for proc in _supervisor_processes():
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            proc.kill()
    stop_health_watchdog_process()
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
