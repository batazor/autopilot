"""Local dev bot lifecycle: detect / start / stop worker + health watchdog.

Used by ``uv run play`` (optional subprocess supervisor) and the dashboard API
(embedded async supervisor in the API process).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

import psutil

from config.paths import repo_root

_SUPERVISOR_MODULE = "worker.supervisor"
_INSTANCE_RUNNER_MODULE = "worker.instance_runner"
_EMBEDDED_THREAD_NAME = "wos-async-services"
BotMode = Literal["supervisor", "embedded"] | None
logger = logging.getLogger(__name__)

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
            matches_console_script = any(
                Path(arg).name == "bot"
                and Path(arg).parent.name == "bin"
                and Path(arg).parent.parent.name == ".venv"
                for arg in cmdline
            )
            verdict = (matches_module or matches_console_script) and Path(proc.cwd()).resolve() == Path(repo).resolve()
        _PROCESS_VERDICT_CACHE[key] = verdict
        _PROCESS_VERDICT_CACHE.move_to_end(key)
        while len(_PROCESS_VERDICT_CACHE) > _PROCESS_VERDICT_CACHE_MAX:
            _PROCESS_VERDICT_CACHE.popitem(last=False)
        return verdict
    except (psutil.Error, OSError):
        return False


def _supervisor_processes(repo: os.PathLike[str] | None = None) -> list[psutil.Process]:
    root = Path(repo or repo_root())
    try:
        return [
            proc
            for proc in psutil.process_iter()
            if _is_repo_supervisor_process(proc, root)
        ]
    except (psutil.Error, OSError):
        logger.debug("failed to inspect local supervisor processes", exc_info=True)
        return []


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


# A worker refreshes ``wos:instance:<id>:state.last_seen_at`` every ~2 s. Treat a
# heartbeat within this window as "a worker is alive". Wider than the dashboard's
# 10 s "live" cutoff so a one-shot `bot status` doesn't flap when it happens to
# land a few seconds after the last beat.
_HEARTBEAT_FRESH_SECONDS = 15.0


def _local_process_status() -> dict[str, Any]:
    """Liveness of a worker stack **this host spawned** (psutil + thread scan).

    This is what ``start``/``stop`` gate on — they must only act on processes we
    own. ``bot_status`` layers a Redis-heartbeat fallback on top for reporting.
    """
    try:
        sup = _supervisor_processes()
    except Exception:
        logger.debug("bot status process scan failed", exc_info=True)
        sup = []
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


def _external_worker_heartbeats(max_age_s: float = _HEARTBEAT_FRESH_SECONDS) -> list[dict[str, Any]]:
    """Instances whose Redis heartbeat is fresh, regardless of whether *this*
    host owns the worker process.

    A worker started out-of-band — the dashboard API process, a standalone
    ``uv run bot``, or a container — heartbeats into the same Redis but is
    invisible to the psutil scan, which is what made ``bot status`` report a
    false ``running: false`` while the dashboard correctly showed it live.
    """
    try:
        from dashboard.redis_client import get_redis

        client = get_redis()
    except Exception:
        logger.debug("heartbeat scan: redis unavailable", exc_info=True)
        return []
    now = time.time()
    live: list[dict[str, Any]] = []
    try:
        for key in client.scan_iter(match="wos:instance:*:state"):
            kname = key.decode() if isinstance(key, bytes) else key
            raw = client.hget(kname, "last_seen_at")
            if not raw:
                continue
            ls = raw.decode() if isinstance(raw, bytes) else raw
            try:
                age = now - float(ls)
            except (TypeError, ValueError):
                continue
            if age <= max_age_s:
                parts = kname.split(":")
                iid = parts[2] if len(parts) >= 4 else kname
                live.append({"instance_id": iid, "age_s": round(age, 1)})
    except Exception:
        logger.debug("heartbeat scan failed", exc_info=True)
        return []
    live.sort(key=lambda d: d["age_s"])
    return live


def bot_status() -> dict[str, Any]:
    """Return ``{running, mode, pid, processes}`` for the worker stack.

    Detection order: a worker process this host spawned (``supervisor`` /
    ``embedded``), else a fresh Redis heartbeat from a worker started out-of-band
    (``external`` — dashboard API process, standalone ``uv run bot``, container).
    The heartbeat fallback keeps ``status`` honest when the psutil scan can't see
    the process; ``start``/``stop`` still gate on the process scan only.

    ``processes`` is the full list of detected local supervisors (typically 1,
    but dev cycles or accidental double-starts can leave several behind). The
    legacy ``pid`` field stays for back-compat and points at the first one.
    """
    local = _local_process_status()
    if local["running"]:
        return local
    external = _external_worker_heartbeats()
    if external:
        return {
            "running": True,
            "mode": "external",
            "pid": None,
            "processes": [],
            "live_instances": external,
        }
    return {"running": False, "mode": None, "pid": None, "processes": []}


def start_supervisor_subprocess() -> dict[str, Any]:
    """Spawn ``python -m worker.supervisor`` plus the health watchdog."""
    status = _local_process_status()
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
    status = _local_process_status()
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


def _clear_focus(instance_id: str) -> None:
    """Best-effort: drop the ``focus_scenario`` flag for a reaped instance worker."""
    try:
        from dashboard.redis_client import get_redis
        from worker import focus_mode

        focus_mode.clear_focus(get_redis(), instance_id)
    except Exception:
        logger.debug("failed to clear focus for %s", instance_id, exc_info=True)


def _stop_all_instance_workers() -> list[str]:
    """Terminate every isolated ``instance_runner`` worker + clear its focus flag.

    These are always spawned by this host's long-lived API process (the fish-detect
    Play primitive) and were previously invisible to ``stop_local_bot`` — so a Stop
    bot left them running and logging. Returns the ids reaped (best-effort).
    """
    reaped: list[str] = []
    for proc, instance_id in _all_instance_runner_processes():
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            proc.kill()
        _clear_focus(instance_id)
        reaped.append(instance_id)
    return reaped


def stop_local_bot(*, join_timeout_s: float = 5.0) -> dict[str, Any]:
    """Stop whichever local bot mode is active, plus any isolated instance workers.

    The supervisor/embedded stop gates on the process scan, not ``bot_status`` — we
    can only stop a worker this host spawned; an ``external`` heartbeat is not ours
    to terminate. Isolated ``instance_runner`` workers are reaped unconditionally:
    they are always spawned by this host's long-lived API process, so Stop bot owns
    them too.
    """
    status = _local_process_status()
    if status["running"]:
        if status["mode"] == "supervisor":
            stop_supervisor_subprocess()
        else:
            stop_embedded_bot(join_timeout_s=join_timeout_s)
    _stop_all_instance_workers()
    return bot_status()


# ── Isolated single-instance worker ─────────────────────────────────────────
# Runs the worker for one device only (``python -m worker.instance_runner
# <id>``) — no scheduler, no other instances. Lets the fish-detect Play button
# play one scenario in isolation instead of starting the whole fleet.


def _instance_runner_id(proc: psutil.Process, repo: os.PathLike[str]) -> str | None:
    """Instance id if ``proc`` is this repo's ``instance_runner``, else ``None``.

    Launch form is ``python -m worker.instance_runner <id>``, so the id is the
    argv token right after the module name.
    """
    try:
        if proc.pid == os.getpid():
            return None
        with proc.oneshot():
            cmdline = proc.cmdline()
            module_idx = next(
                (
                    idx + 1
                    for idx, arg in enumerate(cmdline)
                    if arg == "-m"
                    and idx + 1 < len(cmdline)
                    and cmdline[idx + 1] == _INSTANCE_RUNNER_MODULE
                ),
                None,
            )
            if module_idx is None:
                return None
            id_idx = module_idx + 1
            iid = cmdline[id_idx].strip() if id_idx < len(cmdline) else ""
            if not iid:
                return None
            if Path(proc.cwd()).resolve() != Path(repo).resolve():
                return None
            return iid
    except (psutil.Error, OSError):
        return None


def _is_repo_instance_runner_process(
    proc: psutil.Process, repo: os.PathLike[str], instance_id: str
) -> bool:
    return _instance_runner_id(proc, repo) == instance_id


def _all_instance_runner_processes(
    repo: os.PathLike[str] | None = None,
) -> list[tuple[psutil.Process, str]]:
    """Every isolated ``instance_runner`` worker for this repo, paired with its id.

    Unlike :func:`_instance_runner_processes` this is not filtered by a specific
    instance — it finds *all* isolated single-instance workers so a blanket stop
    (``stop_local_bot``) can reap them and clear their focus flag.
    """
    root = Path(repo or repo_root())
    found: list[tuple[psutil.Process, str]] = []
    try:
        procs = list(psutil.process_iter())
    except (psutil.Error, OSError):
        logger.debug("failed to inspect instance-runner processes", exc_info=True)
        return found
    for proc in procs:
        iid = _instance_runner_id(proc, root)
        if iid is not None:
            found.append((proc, iid))
    return found


def _instance_runner_processes(
    instance_id: str, repo: os.PathLike[str] | None = None
) -> list[psutil.Process]:
    root = Path(repo or repo_root())
    try:
        return [
            proc
            for proc in psutil.process_iter()
            if _is_repo_instance_runner_process(proc, root, instance_id)
        ]
    except (psutil.Error, OSError):
        logger.debug("failed to inspect instance-runner processes", exc_info=True)
        return []


def instance_worker_status(instance_id: str) -> dict[str, Any]:
    """``{running, instance_id, pid}`` for the isolated worker of one device."""
    procs = _instance_runner_processes(instance_id)
    if procs:
        procs.sort(key=lambda p: p.pid)
        return {"running": True, "instance_id": instance_id, "pid": procs[0].pid}
    return {"running": False, "instance_id": instance_id, "pid": None}


def start_instance_worker(instance_id: str) -> dict[str, Any]:
    """Spawn ``python -m worker.instance_runner <id>`` for one device only."""
    instance_id = (instance_id or "").strip()
    if not instance_id:
        msg = "instance_id required"
        raise ValueError(msg)
    status = instance_worker_status(instance_id)
    if status["running"]:
        return status
    repo = repo_root()
    kwargs: dict[str, object] = {"cwd": str(repo), "env": os.environ.copy()}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", _INSTANCE_RUNNER_MODULE, instance_id],
        **kwargs,  # type: ignore[arg-type]
    )
    return {"running": True, "instance_id": instance_id, "pid": proc.pid}


def stop_instance_worker(instance_id: str) -> dict[str, Any]:
    """Terminate the isolated worker for one device."""
    for proc in _instance_runner_processes(instance_id):
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            proc.kill()
    return instance_worker_status(instance_id)
