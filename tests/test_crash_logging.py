"""Tests for ``config.crash_logging`` — uncaught exceptions logged at ERROR."""
from __future__ import annotations

import logging
import sys
import threading

import pytest

import config.crash_logging as crash_logging


def _boom(msg: str) -> None:
    raise ValueError(msg)


@pytest.fixture
def fresh_hooks():
    """Restore process-global hooks + the module install flag around each test."""
    saved_excepthook = sys.excepthook
    saved_threadhook = threading.excepthook
    crash_logging._installed = False
    try:
        yield
    finally:
        sys.excepthook = saved_excepthook
        threading.excepthook = saved_threadhook
        crash_logging._installed = False


def test_uncaught_exception_logged_at_error(fresh_hooks, caplog):
    crash_logging.install_crash_logging()

    try:
        _boom("kaboom")
    except ValueError:
        exc_info = sys.exc_info()

    with caplog.at_level(logging.ERROR, logger="wos.crash"):
        sys.excepthook(*exc_info)

    records = [r for r in caplog.records if r.name == "wos.crash"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
    assert "kaboom" in caplog.text


def test_keyboard_interrupt_not_logged(fresh_hooks, caplog):
    crash_logging.install_crash_logging()

    with caplog.at_level(logging.ERROR, logger="wos.crash"):
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

    assert not [r for r in caplog.records if r.name == "wos.crash"]


def test_chains_previous_excepthook(fresh_hooks):
    seen: list[type] = []

    def _record_hook(exc_type, *_rest):
        seen.append(exc_type)

    sys.excepthook = _record_hook

    crash_logging.install_crash_logging()
    sys.excepthook(RuntimeError, RuntimeError("x"), None)

    assert seen == [RuntimeError], "original hook still called after ours"


def test_install_is_idempotent(fresh_hooks):
    crash_logging.install_crash_logging()
    hook_after_first = sys.excepthook
    crash_logging.install_crash_logging()
    assert sys.excepthook is hook_after_first, "second install must be a no-op"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_exception_logged_at_error(fresh_hooks, caplog):
    crash_logging.install_crash_logging()

    with caplog.at_level(logging.ERROR, logger="wos.crash"):
        t = threading.Thread(target=_boom, args=("thread boom",), name="boomer")
        t.start()
        t.join()

    records = [r for r in caplog.records if r.name == "wos.crash"]
    assert len(records) == 1
    assert "thread boom" in caplog.text
    assert "boomer" in records[0].getMessage()
