"""API shutdown noise suppression."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import (
    _is_shutdown_exception,
    _SuppressUvicornShutdownNoiseFilter,
    app,
)


@app.get("/__test_unhandled_error")
def _test_unhandled_error() -> None:
    msg = "diagnostic boom"
    raise RuntimeError(msg)


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


def test_lifespan_stops_local_bot_on_shutdown() -> None:
    with (
        patch("worker.local_bot.stop_local_bot", return_value={"running": False}) as stop,
        TestClient(app) as client,
    ):
        assert client.get("/health").status_code in {200, 503}

    stop.assert_called_once_with(join_timeout_s=2.0)


def test_unhandled_api_errors_return_diagnostic_json() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.get("/__test_unhandled_error")

    assert res.status_code == 500
    body = res.json()
    assert body["detail"] == "Unexpected API error while handling GET /__test_unhandled_error"
    assert body["error"] == {"type": "RuntimeError", "message": "diagnostic boom"}
    assert body["request_id"]
