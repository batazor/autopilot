"""Configure root logging to **stdout** (terminal), not Redis."""

from __future__ import annotations

import contextlib
import logging
import os
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


class _AnsiLevelFormatter(logging.Formatter):
    _RESET = "\x1b[0m"
    _COLORS: dict[int, str] = {
        logging.DEBUG: "\x1b[90m",  # bright black / grey
        logging.INFO: "\x1b[32m",  # green
        logging.WARNING: "\x1b[33m",  # yellow
        logging.ERROR: "\x1b[31m",  # red
        logging.CRITICAL: "\x1b[1;31m",  # bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        original = record.levelname
        color = self._COLORS.get(record.levelno)
        if color:
            record.levelname = f"{color}{original}{self._RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = original


def _should_colorize(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False
    with contextlib.suppress(Exception):
        return bool(isatty())
    return False


def setup_stdout_logging(level: int = logging.INFO) -> None:
    stream = _stdout_for_logs()
    for s in (stream, sys.stdout, getattr(sys, "__stdout__", None)):
        if s is not None:
            _try_line_buffer(s)

    fmt = "%(levelname)s %(name)s %(message)s"
    if _should_colorize(stream):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(_AnsiLevelFormatter(fmt=fmt))
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(level=level, format=fmt, stream=stream, force=True)
