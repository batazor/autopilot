from __future__ import annotations

import logging
import threading
from typing import Any

import ui.bot_services as bot_services


def test_shutdown_hooks_skip_signals_outside_main_thread(monkeypatch: Any) -> None:
    main_thread = threading.main_thread()

    monkeypatch.setattr(bot_services, "_hooks_installed", False)
    monkeypatch.setattr(bot_services, "_previous_signal_handlers", {})
    monkeypatch.setattr(bot_services.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(bot_services.threading, "current_thread", lambda: object())
    monkeypatch.setattr(bot_services.threading, "main_thread", lambda: main_thread)

    def fail_signal(*_args: object) -> None:
        raise AssertionError("signal.signal must not be called outside the main thread")

    monkeypatch.setattr(bot_services.signal, "signal", fail_signal)

    bot_services._install_shutdown_hooks()


def test_ensure_health_watchdog_reuses_existing_process(monkeypatch: Any) -> None:
    class ExistingProcess:
        pid = 12345

    spawned: list[object] = []

    monkeypatch.setattr(bot_services, "_health_proc", None)
    monkeypatch.setattr(
        bot_services,
        "_existing_health_watchdog_process",
        lambda _repo: ExistingProcess(),
    )
    monkeypatch.setattr(bot_services.subprocess, "Popen", lambda *a, **kw: spawned.append((a, kw)))

    bot_services._ensure_health_watchdog()

    assert spawned == []
    assert bot_services._health_proc is None


def test_ensure_health_watchdog_logs_existing_process_once(monkeypatch: Any, caplog: Any) -> None:
    class ExistingProcess:
        pid = 12345

    monkeypatch.setattr(bot_services, "_health_proc", None)
    monkeypatch.setattr(bot_services, "_known_health_watchdog_pid", None)
    monkeypatch.setattr(
        bot_services,
        "_existing_health_watchdog_process",
        lambda _repo: ExistingProcess(),
    )

    with caplog.at_level(logging.INFO, logger=bot_services.__name__):
        bot_services._ensure_health_watchdog()
        bot_services._ensure_health_watchdog()

    messages = [
        record.getMessage()
        for record in caplog.records
        if "Game health watchdog subprocess already running" in record.getMessage()
    ]
    assert messages == ["Game health watchdog subprocess already running pid=12345"]


def test_stop_health_watchdog_stops_discovered_process(monkeypatch: Any) -> None:
    class ExistingProcess:
        def __init__(self) -> None:
            self.terminated = False
            self.killed = False
            self.waited = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float) -> None:
            assert timeout == 8.0
            self.waited = True

        def kill(self) -> None:
            self.killed = True

    existing = ExistingProcess()

    monkeypatch.setattr(bot_services, "_health_proc", None)
    monkeypatch.setattr(
        bot_services,
        "_health_watchdog_processes",
        lambda _repo: [existing],
    )

    bot_services._stop_health_watchdog()

    assert existing.terminated is True
    assert existing.waited is True
    assert existing.killed is False
