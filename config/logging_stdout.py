"""Configure root logging to **stdout** (terminal), not Redis."""

from __future__ import annotations

import contextlib
import logging
import sys
from io import UnsupportedOperation
from typing import TextIO


def _stdout_for_logs() -> TextIO:
    """Prefer the interpreter's original stdout (fd 1).

    Some runners / test harnesses replace ``sys.stdout`` with a capturing
    wrapper; bot logs should still land in the real terminal when possible.
    """
    raw = getattr(sys, "__stdout__", None)
    if raw is not None and not raw.closed:
        return raw
    return sys.stdout


def _try_line_buffer(stream: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    with contextlib.suppress(OSError, ValueError, UnsupportedOperation):
        reconfigure(line_buffering=True)


def setup_stdout_logging(level: int = logging.INFO) -> None:
    stream = _stdout_for_logs()
    for s in (stream, sys.stdout, getattr(sys, "__stdout__", None)):
        if s is not None:
            _try_line_buffer(s)

    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s %(message)s",
        stream=stream,
        force=True,
    )
