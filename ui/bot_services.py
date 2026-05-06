"""Embedded bot: start asyncio supervisor thread exactly once.

Used by ``ui/app.py`` and individual ``views/*.py`` when Streamlit is launched on that file,
so workers still run without going through ``app.py``.
"""

from __future__ import annotations

import logging
import threading

from config.logging_stdout import setup_stdout_logging

_started = False
_lock = threading.Lock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None


def ensure_embedded_bot() -> None:
    """Start ``run_forever_async`` in a daemon thread if not already running."""
    global _started, _stop_event, _thread
    with _lock:
        if _started:
            return
        setup_stdout_logging()

        import asyncio

        from worker.async_supervisor import run_forever_async

        def _run_loop() -> None:
            assert _stop_event is not None
            asyncio.run(run_forever_async(stop_event=_stop_event))

        _stop_event = threading.Event()
        _thread = threading.Thread(target=_run_loop, daemon=True, name="wos-async-services")
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
