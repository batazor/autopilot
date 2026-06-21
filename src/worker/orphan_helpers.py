from __future__ import annotations

import logging
import time
from pathlib import Path

import psutil

from config.paths import repo_root

logger = logging.getLogger(__name__)

_SCK_HELPER_REL = Path(".cache") / "sck" / "sck_capture_helper"


def _proc_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except psutil.Error:
        return []


def _is_orphaned_sck_helper(proc: psutil.Process, helper_path: Path) -> bool:
    if proc.pid == 0:
        return False
    try:
        if proc.ppid() != 1:
            return False
    except psutil.Error:
        return False

    helper_s = str(helper_path)
    cmdline = _proc_cmdline(proc)
    if any(part == helper_s for part in cmdline):
        return True

    try:
        return str(proc.exe()) == helper_s
    except psutil.Error:
        return False


def cleanup_orphaned_sck_capture_helpers(*, root: Path | None = None, timeout_s: float = 2.0) -> list[int]:
    """Terminate old ScreenCaptureKit helper processes orphaned under launchd.

    The active bot uses ``scrcpy`` for screenshots, but older local capture
    experiments can leave ``.cache/sck/sck_capture_helper`` processes alive
    with ``PPID=1``. They keep burning CPU for days. Only helpers from this
    checkout and already orphaned under launchd are touched.
    """
    base = (root or repo_root()).resolve()
    helper_path = base / _SCK_HELPER_REL
    if not helper_path.exists():
        return []

    targets = [
        proc
        for proc in psutil.process_iter(["pid", "ppid", "cmdline", "exe"])
        if _is_orphaned_sck_helper(proc, helper_path)
    ]

    if not targets:
        return []

    pids = [proc.pid for proc in targets]
    for proc in targets:
        try:
            proc.terminate()
        except psutil.Error:
            continue

    gone, alive = psutil.wait_procs(targets, timeout=max(0.0, timeout_s))
    del gone
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=1.0)

    # Give launchd/process table a tiny breath so the follow-up log does not
    # race with just-terminated rows still visible to psutil.
    time.sleep(0.05)
    logger.warning("Cleaned up orphaned SCK capture helper process(es): %s", pids)
    return pids
