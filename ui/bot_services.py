"""Embedded bot: start asyncio supervisor thread exactly once.

Used by ``ui/app.py`` and individual ``views/*.py`` when Streamlit is launched on that file,
so workers still run without going through ``app.py``.
"""

from __future__ import annotations

import atexit
import logging
import signal
import threading
from types import FrameType

from config.logging_stdout import setup_stdout_logging

_THREAD_NAME = "wos-async-services"

_started = False
_lock = threading.RLock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None
_hooks_installed = False
_previous_signal_handlers: dict[int, signal.Handlers] = {}


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


def ensure_embedded_bot() -> None:
    """Start ``run_forever_async`` in a daemon thread if not already running."""
    global _started, _stop_event, _thread
    with _lock:
        if _started:
            return
        existing = _existing_supervisor_thread()
        if existing is not None:
            # Another module instance already started the supervisor.
            _thread = existing
            _started = True
            return
        setup_stdout_logging()

        import asyncio

        from worker.async_supervisor import run_forever_async

        def _run_loop() -> None:
            assert _stop_event is not None
            asyncio.run(run_forever_async(stop_event=_stop_event))

        _stop_event = threading.Event()
        _thread = threading.Thread(target=_run_loop, daemon=True, name=_THREAD_NAME)
        _thread.start()
        logging.getLogger(__name__).info("Embedded bot thread started (async supervisor)")
        _started = True
        _install_shutdown_hooks()


def stop_embedded_bot(*, join_timeout_s: float = 5.0) -> None:
    """Request a clean embedded supervisor shutdown."""
    global _started, _stop_event, _thread
    with _lock:
        stop_event = _stop_event
        thread = _thread
        if not _started or stop_event is None or thread is None:
            _started = False
            _stop_event = None
            _thread = None
            return

        logging.getLogger(__name__).warning("Stopping embedded bot thread")
        stop_event.set()

    thread.join(timeout=join_timeout_s)

    with _lock:
        if thread.is_alive():
            logging.getLogger(__name__).warning(
                "Embedded bot thread did not stop within %.1fs", join_timeout_s
            )
            return
        _started = False
        _stop_event = None
        _thread = None


def restart_embedded_bot(*, join_timeout_s: float = 5.0) -> None:
    """Stop and start the embedded async supervisor thread."""
    logging.getLogger(__name__).warning("Restarting embedded bot thread")
    stop_embedded_bot(join_timeout_s=join_timeout_s)
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
    if callable(previous):
        previous(signum, frame)
    elif previous == signal.SIG_DFL:
        if signum == signal.SIGINT:
            signal.default_int_handler(signum, frame)
        raise SystemExit(0)
