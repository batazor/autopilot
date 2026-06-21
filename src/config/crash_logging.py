"""Route uncaught exceptions (crashes / "panics") through ``stdlib logging``.

By default an unhandled exception in the main thread or a worker thread prints a
traceback to ``stderr`` via ``sys.excepthook`` — it never reaches the logging
handlers, so it doesn't get shipped to Loki by the OTLP log handler. Installing
these hooks logs the crash at ``ERROR`` with full ``exc_info``, so it flows
through the same pipeline as any other error line (stdout + OTLP → Loki) while
still falling through to the original hook for the usual stderr traceback.

asyncio task exceptions are already logged by the loop's default exception
handler (``logging.getLogger("asyncio").error(..., exc_info=...)``), so they
ship without extra wiring here.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger("wos.crash")

_installed = False


def install_crash_logging() -> None:
    """Install ``sys``/``threading`` excepthooks that log crashes at ERROR.

    Idempotent per process. Chains to the previously-installed hooks so the
    default stderr traceback still prints.
    """
    global _installed
    if _installed:
        return

    prev_excepthook = sys.excepthook

    def _excepthook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        # Ctrl+C is a normal exit, not a crash worth shipping.
        if not issubclass(exc_type, KeyboardInterrupt):
            logger.error("uncaught exception", exc_info=(exc_type, exc, tb))
        prev_excepthook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    prev_threadhook = threading.excepthook

    def _threadhook(args: threading.ExceptHookArgs) -> None:
        # ``SystemExit`` raised in a thread is intentional teardown, not a crash.
        if args.exc_type is not None and not issubclass(args.exc_type, SystemExit):
            logger.error(
                "uncaught exception in thread %s",
                args.thread.name if args.thread else "?",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        prev_threadhook(args)

    threading.excepthook = _threadhook

    _installed = True
