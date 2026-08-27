"""Isolated Dreamscape solver lifecycle — the dashboard's Play/Stop backend.

The mini-game needs none of the bot stack: no worker, no scheduler, no queue,
no Redis-gated approvals, no TTL bookkeeping. Play spawns the module's
standalone runner (``games/wos/events/dreamscape_memory/tools/solve.py``) —
which detects words and taps, full stop — and Stop kills it. The runner writes
the rolling live preview itself, so the page's frame/badges/Debug view work
with zero other processes.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from typing import Any

import psutil

from config.paths import repo_root

logger = logging.getLogger(__name__)

_RUNNER_REL = "games/wos/events/dreamscape_memory/tools/solve.py"
_LOG_REL = "logs/dreamscape_solver.log"


def _runner_processes() -> list[psutil.Process]:
    procs: list[psutil.Process] = []
    root = str(repo_root())
    for proc in psutil.process_iter():
        try:
            cmdline = proc.cmdline()
            if any(_RUNNER_REL in arg for arg in cmdline) and proc.cwd() == root:
                procs.append(proc)
        except (psutil.Error, OSError):
            continue
    return procs


def status() -> dict[str, Any]:
    procs = _runner_processes()
    if not procs:
        return {"running": False, "pid": None, "scene": "", "instance_id": ""}
    proc = procs[0]
    scene = instance = ""
    with contextlib.suppress(psutil.Error):
        cmdline = proc.cmdline()
        if "--scene" in cmdline:
            scene = cmdline[cmdline.index("--scene") + 1]
        args = [a for a in cmdline[cmdline.index(_first_runner_arg(cmdline)) + 1 :] if not a.startswith("-")]
        instance = args[0] if args else ""
    return {"running": True, "pid": proc.pid, "scene": scene, "instance_id": instance}


def _first_runner_arg(cmdline: list[str]) -> str:
    return next(arg for arg in cmdline if _RUNNER_REL in arg)


def start(*, instance_id: str, scene: str, mode: str = "solo") -> dict[str, Any]:
    """Spawn the isolated solver for ``scene`` on ``instance_id``.

    One solver at a time: an already-running one is killed first (Play after
    Play is a restart, matching what the operator expects).
    """
    if not instance_id.strip():
        msg = "instance_id is required"
        raise ValueError(msg)
    if not scene.strip():
        msg = "pick a scene first"
        raise ValueError(msg)
    stop()
    # Isolated means isolated: the solver is the only device holder (scrcpy is
    # single-holder, and the worker's dismissers would tap into the round). A
    # running bot worker is stopped before the solver takes over.
    try:
        from worker.local_bot import stop_local_bot

        stop_local_bot()
    except Exception:
        logger.debug("worker stop before solver start failed", exc_info=True)
    repo = repo_root()
    log_path = repo / _LOG_REL
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(repo / _RUNNER_REL),
        instance_id.strip(),
        "--scene",
        scene.strip(),
        "--ttl",
        "0",
    ]
    if mode == "multiplayer":
        cmd.extend(["--mode", "multiplayer"])
    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo),
            env=os.environ.copy(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    logger.info("dreamscape solver started pid=%s scene=%s", proc.pid, scene)
    return {"running": True, "pid": proc.pid, "scene": scene.strip(), "instance_id": instance_id.strip()}


def stop() -> dict[str, Any]:
    """Kill every isolated solver process (children included)."""
    victims: list[psutil.Process] = []
    for proc in _runner_processes():
        with contextlib.suppress(psutil.Error):
            victims.extend(proc.children(recursive=True))
        victims.append(proc)
    for proc in victims:
        with contextlib.suppress(psutil.Error):
            proc.terminate()
    _gone, alive = psutil.wait_procs(victims, timeout=3.0)
    for proc in alive:
        with contextlib.suppress(psutil.Error):
            proc.kill()
    psutil.wait_procs(alive, timeout=3.0)
    return status()


def tail_log(lines: int = 80) -> list[str]:
    path = repo_root() / _LOG_REL
    if not path.is_file():
        return []
    try:
        return path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return []
