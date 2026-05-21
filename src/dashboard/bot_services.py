"""Embedded bot: start asyncio supervisor thread exactly once.

Used by ``ui/app.py`` and individual ``views/*.py`` when Streamlit is launched on that file,
so workers still run without going through ``app.py``.

Also starts **worker.game_health_watchdog** in a separate subprocess so ADB foreground checks
are not delayed by long-running DSL tasks on the asyncio worker loop.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from config.loader import get_settings, load_settings, set_settings
from config.paths import repo_root
from config.redis_health import verify_sync_redis_url
from config.state_store import register_on_save
from scheduler.wake import wake_scheduler

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

_THREAD_NAME = "wos-async-services"
_HEALTH_WATCHDOG_MODULE = "worker.game_health_watchdog"

_started = False
_lock = threading.RLock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None
_health_proc: subprocess.Popen[bytes] | None = None
_known_health_watchdog_pid: int | None = None
_hooks_installed = False
# ``signal.getsignal`` returns either a callable, ``signal.SIG_DFL``/``SIG_IGN``
# (ints), ``signal.Handlers`` enum, or ``None``. Keep the dict permissive so the
# restoration site in ``_handle_shutdown_signal`` can branch on the runtime kind.
_previous_signal_handlers: dict[int, Callable[[int, FrameType | None], Any] | int | signal.Handlers | None] = {}


_state_wake_registered = False


def _register_state_save_wake() -> None:
    """Publish a scheduler wake whenever state.yaml is persisted.

    Idempotent — the state_store registry already dedups, and we guard here
    too so repeated ``ensure_embedded_bot`` calls don't construct extra
    Redis clients."""
    global _state_wake_registered
    if _state_wake_registered:
        return
    try:
        import redis as _redis_sync

        from config.redis_metrics import instrument_redis_client

        url = get_settings().redis.url
        client = _redis_sync.Redis.from_url(url, socket_connect_timeout=5.0)
        instrument_redis_client(client, component="ui")
    except Exception:
        logging.getLogger(__name__).debug(
            "state-save wake registration skipped", exc_info=True
        )
        return

    def _wake() -> None:
        try:
            wake_scheduler(client, {"cmd": "wake", "reason": "state_saved"})
        except Exception:
            logging.getLogger(__name__).debug("state-save wake failed", exc_info=True)

    register_on_save(_wake)
    _state_wake_registered = True


def _existing_supervisor_thread() -> threading.Thread | None:
    """Return a live supervisor thread already running in this process, if any.

    Streamlit may re-import ``ui.bot_services`` (module reload), which resets
    the module-level guards above. To avoid spawning a second supervisor (and
    a second scenarios watchdog observer that would clash with the first via
    fsevents' "already scheduled" RuntimeError), look at the process-wide
    thread list — that survives any number of module reloads.
    """
    for t in threading.enumerate():
        if t.name == _THREAD_NAME and t.is_alive():
            return t
    return None


def _is_health_watchdog_process(proc: psutil.Process, repo: Path) -> bool:
    try:
        if proc.pid == os.getpid():
            return False
        cmdline = proc.cmdline()
        if not any(
            arg == "-m"
            and idx + 1 < len(cmdline)
            and cmdline[idx + 1] == _HEALTH_WATCHDOG_MODULE
            for idx, arg in enumerate(cmdline)
        ):
            return False
        return Path(proc.cwd()).resolve() == repo
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _health_watchdog_processes(repo: Path) -> list[psutil.Process]:
    return [
        proc
        for proc in psutil.process_iter()
        if _is_health_watchdog_process(proc, repo)
    ]


def _existing_health_watchdog_process(repo: Path) -> psutil.Process | None:
    for proc in _health_watchdog_processes(repo):
        return proc
    return None


def ensure_health_watchdog() -> None:
    """Spawn ``python -m worker.game_health_watchdog`` if not already running."""
    global _health_proc, _known_health_watchdog_pid
    with _lock:
        if _health_proc is not None and _health_proc.poll() is None:
            _known_health_watchdog_pid = _health_proc.pid
            return
        _health_proc = None
        repo = repo_root()
        log = logging.getLogger(__name__)
        existing = _existing_health_watchdog_process(repo)
        if existing is not None:
            if _known_health_watchdog_pid != existing.pid:
                log.info("Game health watchdog subprocess already running pid=%s", existing.pid)
            else:
                log.debug("Game health watchdog subprocess already running pid=%s", existing.pid)
            _known_health_watchdog_pid = existing.pid
            return
        try:
            _health_proc = subprocess.Popen(
                [sys.executable, "-m", _HEALTH_WATCHDOG_MODULE],
                cwd=str(repo),
                env=os.environ.copy(),
            )
            _known_health_watchdog_pid = _health_proc.pid
            log.info("Game health watchdog subprocess pid=%s", _health_proc.pid)
        except Exception:
            log.exception("Failed to start game health watchdog subprocess")


def _stop_health_watchdog() -> None:
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
    for existing in _health_watchdog_processes(repo):
        existing.terminate()
        try:
            existing.wait(timeout=8.0)
        except psutil.TimeoutExpired:
            existing.kill()


def ensure_embedded_bot() -> None:
    """Start ``run_forever_async`` in a daemon thread if not already running."""
    global _started, _stop_event, _thread
    with _lock:
        # Streamlit / importlib can reload modules while the supervisor thread
        # keeps running; fresh copies of ``config.loader`` then have no bound
        # ``Settings`` even though workers are alive. App-level service refs
        # (OCR client, scheduler Redis, ...) live in ``app._state``.
        try:
            get_settings()
        except RuntimeError:
            set_settings(load_settings())

        if not _started:
            existing = _existing_supervisor_thread()
            if existing is not None:
                # Another module instance already started the supervisor.
                _thread = existing
                _started = True
                from config.runtime_bootstrap import bootstrap_runtime_observability

                bootstrap_runtime_observability("embedded")
                verify_sync_redis_url(get_settings().redis.url)
                _register_state_save_wake()
            else:
                from config.runtime_bootstrap import bootstrap_runtime_observability

                bootstrap_runtime_observability("embedded")
                verify_sync_redis_url(get_settings().redis.url)
                _register_state_save_wake()

                import asyncio

                from worker.async_supervisor import run_forever_async

                def _run_loop() -> None:
                    assert _stop_event is not None
                    asyncio.run(run_forever_async(stop_event=_stop_event))

                _stop_event = threading.Event()
                _thread = threading.Thread(
                    target=_run_loop, daemon=True, name=_THREAD_NAME
                )
                _thread.start()
                logging.getLogger(__name__).info(
                    "Embedded bot thread started (async supervisor)"
                )
                _started = True
                _install_shutdown_hooks()
        ensure_health_watchdog()


def stop_embedded_bot(*, join_timeout_s: float = 5.0) -> bool:
    """Request a clean embedded supervisor shutdown.

    Returns True when the thread is gone (or was never running). Returns False
    when the thread did not stop within ``join_timeout_s`` — in that case the
    module-level state is *not* cleared, so a subsequent ``ensure_embedded_bot``
    will not silently no-op into a half-stopped state.
    """
    global _started, _stop_event, _thread
    _stop_health_watchdog()
    stop_event, thread = request_embedded_bot_stop()
    if stop_event is None or thread is None:
        return True

    thread.join(timeout=join_timeout_s)

    with _lock:
        if thread.is_alive():
            logging.getLogger(__name__).warning(
                "Embedded bot thread did not stop within %.1fs", join_timeout_s
            )
            return False
        _started = False
        _stop_event = None
        _thread = None
        return True


def request_embedded_bot_stop() -> tuple[threading.Event | None, threading.Thread | None]:
    """Signal the embedded supervisor to stop without blocking the caller."""
    global _started, _stop_event, _thread
    with _lock:
        stop_event = _stop_event
        thread = _thread
        if not _started or stop_event is None or thread is None:
            _started = False
            _stop_event = None
            _thread = None
            return None, None

        logging.getLogger(__name__).warning("Stopping embedded bot thread")
        stop_event.set()
        return stop_event, thread


def restart_embedded_bot(*, join_timeout_s: float = 5.0) -> None:
    """Stop and start the embedded async supervisor thread.

    Raises ``RuntimeError`` if the existing supervisor thread won't stop within
    ``join_timeout_s``. Calling ``ensure_embedded_bot`` after a failed stop
    would no-op (``_started`` is still True) and lie to the operator that the
    bot restarted, so we fail loudly instead.
    """
    logging.getLogger(__name__).warning("Restarting embedded bot thread")
    if not stop_embedded_bot(join_timeout_s=join_timeout_s):
        msg = (
            f"Embedded bot thread did not stop within {join_timeout_s:.1f}s; "
            "refusing to start a duplicate supervisor"
        )
        raise RuntimeError(
            msg
        )
    ensure_embedded_bot()


def _install_shutdown_hooks() -> None:
    global _hooks_installed
    with _lock:
        if _hooks_installed:
            return
        atexit.register(stop_embedded_bot)
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                _previous_signal_handlers[int(sig)] = signal.getsignal(sig)
                signal.signal(sig, _handle_shutdown_signal)
        _hooks_installed = True


def _handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
    stop_embedded_bot(join_timeout_s=2.0)

    previous = _previous_signal_handlers.get(signum)
    if callable(previous) and not isinstance(previous, int):
        previous(signum, frame)
    elif previous == signal.SIG_DFL:
        if signum == signal.SIGINT:
            signal.default_int_handler(signum, frame)
        raise SystemExit(0)
