"""API shutdown noise suppression."""
from __future__ import annotations

import asyncio
import logging

from api.main import (
    _is_shutdown_exception,
    _SuppressUvicornShutdownNoiseFilter,
)


def test_is_shutdown_exception_cancelled() -> None:
    assert _is_shutdown_exception(asyncio.CancelledError())


def test_is_shutdown_exception_keyboard_interrupt() -> None:
    assert _is_shutdown_exception(KeyboardInterrupt())


def test_is_shutdown_exception_chained() -> None:
    inner = asyncio.CancelledError()
    outer = RuntimeError("during shutdown")
    outer.__cause__ = inner
    assert _is_shutdown_exception(outer)


def test_is_shutdown_exception_other() -> None:
    assert not _is_shutdown_exception(ValueError("nope"))


def test_uvicorn_filter_drops_cancelled_asgi_log() -> None:
    filt = _SuppressUvicornShutdownNoiseFilter()
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Exception in ASGI application\n",
        args=(),
        exc_info=(asyncio.CancelledError, asyncio.CancelledError(), None),
    )
    assert not filt.filter(record)


def test_uvicorn_filter_keeps_real_errors() -> None:
    filt = _SuppressUvicornShutdownNoiseFilter()
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Exception in ASGI application\n",
        args=(),
        exc_info=(ValueError, ValueError("boom"), None),
    )
    assert filt.filter(record)
