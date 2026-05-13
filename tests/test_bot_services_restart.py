from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import ui.bot_services as bot_services


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: Any) -> Any:
    """Avoid bleeding state between tests — the bot_services module is global."""
    monkeypatch.setattr(bot_services, "_started", False)
    monkeypatch.setattr(bot_services, "_stop_event", None)
    monkeypatch.setattr(bot_services, "_thread", None)
    monkeypatch.setattr(bot_services, "_stop_health_watchdog", lambda: None)
    yield


def test_stop_embedded_bot_returns_true_when_nothing_running() -> None:
    """No supervisor active → stop is trivially successful."""
    assert bot_services.stop_embedded_bot() is True


def test_stop_embedded_bot_returns_false_when_thread_will_not_stop(
    monkeypatch: Any,
) -> None:
    """A thread that ignores the stop event must produce ``False``, and the
    module state must remain marked as ``_started`` so a later restart cannot
    falsely no-op into a duplicate supervisor."""

    stop_event = threading.Event()
    started = threading.Event()

    def _hung_loop() -> None:
        started.set()
        # Never honor the stop_event — simulate a stuck async loop.
        time.sleep(5)

    thread = threading.Thread(target=_hung_loop, daemon=True, name="wos-async-services")
    thread.start()
    started.wait(timeout=1.0)

    monkeypatch.setattr(bot_services, "_started", True)
    monkeypatch.setattr(bot_services, "_stop_event", stop_event)
    monkeypatch.setattr(bot_services, "_thread", thread)

    ok = bot_services.stop_embedded_bot(join_timeout_s=0.1)
    assert ok is False
    # State preserved so restart can detect the failure.
    assert bot_services._started is True
    assert bot_services._thread is thread


def test_restart_embedded_bot_raises_when_thread_does_not_stop(
    monkeypatch: Any,
) -> None:
    """``restart_embedded_bot`` must raise instead of silently no-op'ing
    when the supervisor thread is stuck — otherwise the operator sees a
    success toast for a restart that never happened."""

    stop_event = threading.Event()
    started = threading.Event()

    def _hung_loop() -> None:
        started.set()
        time.sleep(5)

    thread = threading.Thread(target=_hung_loop, daemon=True, name="wos-async-services")
    thread.start()
    started.wait(timeout=1.0)

    monkeypatch.setattr(bot_services, "_started", True)
    monkeypatch.setattr(bot_services, "_stop_event", stop_event)
    monkeypatch.setattr(bot_services, "_thread", thread)

    ensure_called: list[bool] = []

    def _fail_ensure() -> None:
        ensure_called.append(True)

    monkeypatch.setattr(bot_services, "ensure_embedded_bot", _fail_ensure)

    with pytest.raises(RuntimeError, match="did not stop"):
        bot_services.restart_embedded_bot(join_timeout_s=0.1)

    assert ensure_called == [], "must not start a new supervisor while the old one is stuck"
