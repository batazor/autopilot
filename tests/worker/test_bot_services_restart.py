from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import dashboard.bot_services as bot_services


@pytest.fixture(autouse=True)
def _reset_module_state(mocker) -> Any:
    """Avoid bleeding state between tests — the bot_services module is global."""
    mocker.patch.object(bot_services, "_started", new=False)
    mocker.patch.object(bot_services, "_stop_event", new=None)
    mocker.patch.object(bot_services, "_stop_in_progress", new=False)
    mocker.patch.object(bot_services, "_thread", new=None)
    mocker.patch.object(bot_services, "_stop_health_watchdog", new=lambda: None)
    mocker.patch.object(bot_services, "ensure_health_watchdog", new=lambda: None)
    return


def test_stop_embedded_bot_returns_true_when_nothing_running() -> None:
    """No supervisor active → stop is trivially successful."""
    assert bot_services.stop_embedded_bot() is True


def test_stop_embedded_bot_returns_false_when_thread_will_not_stop(
    mocker,
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

    mocker.patch.object(bot_services, "_started", new=True)
    mocker.patch.object(bot_services, "_stop_event", new=stop_event)
    mocker.patch.object(bot_services, "_thread", new=thread)
    stops: list[bool] = []
    ensures: list[bool] = []
    mocker.patch.object(
        bot_services, "_stop_health_watchdog", new=lambda: stops.append(True)
    )
    mocker.patch.object(
        bot_services, "ensure_health_watchdog", new=lambda: ensures.append(True)
    )

    ok = bot_services.stop_embedded_bot(join_timeout_s=0.1)
    assert ok is False
    assert stops == []
    assert ensures == [True]
    # State preserved so restart can detect the failure.
    assert bot_services._started is True
    assert bot_services._thread is thread


def test_repeated_stop_does_not_wait_again_for_stuck_thread(
    mocker,
) -> None:
    stop_event = threading.Event()
    started = threading.Event()

    def _hung_loop() -> None:
        started.set()
        time.sleep(5)

    thread = threading.Thread(target=_hung_loop, daemon=True, name="wos-async-services")
    thread.start()
    started.wait(timeout=1.0)

    mocker.patch.object(bot_services, "_started", new=True)
    mocker.patch.object(bot_services, "_stop_event", new=stop_event)
    mocker.patch.object(bot_services, "_thread", new=thread)

    assert bot_services.stop_embedded_bot(join_timeout_s=0.1) is False
    t0 = time.monotonic()
    assert bot_services.stop_embedded_bot(join_timeout_s=5.0) is False
    assert time.monotonic() - t0 < 0.5


def test_restart_embedded_bot_raises_when_thread_does_not_stop(
    mocker,
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

    mocker.patch.object(bot_services, "_started", new=True)
    mocker.patch.object(bot_services, "_stop_event", new=stop_event)
    mocker.patch.object(bot_services, "_thread", new=thread)

    ensure_called: list[bool] = []

    def _fail_ensure() -> None:
        ensure_called.append(True)

    mocker.patch.object(bot_services, "ensure_embedded_bot", new=_fail_ensure)

    with pytest.raises(RuntimeError, match="did not stop"):
        bot_services.restart_embedded_bot(join_timeout_s=0.1)

    assert ensure_called == [], "must not start a new supervisor while the old one is stuck"
