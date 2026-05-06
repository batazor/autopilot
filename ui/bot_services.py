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


def ensure_embedded_bot() -> None:
    """Start ``run_forever_async`` in a daemon thread if not already running."""
    global _started
    with _lock:
        if _started:
            return
        setup_stdout_logging()

        import asyncio

        from worker.async_supervisor import run_forever_async

        def _run_loop() -> None:
            asyncio.run(run_forever_async())

        threading.Thread(target=_run_loop, daemon=True, name="wos-async-services").start()
        logging.getLogger(__name__).info("Embedded bot thread started (async supervisor)")
        _started = True
