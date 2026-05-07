"""Embedded bot: start asyncio supervisor thread exactly once.

Used by ``ui/app.py`` and individual ``views/*.py`` when Streamlit is launched on that file,
so workers still run without going through ``app.py``.
"""

from __future__ import annotations

import logging
import threading

from config.logging_stdout import setup_stdout_logging

_THREAD_NAME = "wos-async-services"

_started = False
_lock = threading.Lock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None


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


def restart_embedded_bot(*, join_timeout_s: float = 5.0) -> None:
    """Stop and start the embedded async supervisor thread."""
    global _started, _stop_event, _thread
    with _lock:
        if not _started or _stop_event is None or _thread is None:
            # Nothing running yet; just start.
            _started = False
            ensure_embedded_bot()
            return

        logging.getLogger(__name__).warning("Restarting embedded bot thread")
        _stop_event.set()
        # Wait a bit for a clean shutdown (best-effort; thread is daemon).
        _thread.join(timeout=join_timeout_s)
        _started = False
        _stop_event = None
        _thread = None
        ensure_embedded_bot()
