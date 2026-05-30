from __future__ import annotations

import logging
import threading
from typing import Any

import dashboard.bot_services as bot_services
from worker import health_watchdog_process


def test_shutdown_hooks_skip_signals_outside_main_thread(mocker) -> None:
    main_thread = threading.main_thread()

    mocker.patch.object(bot_services, "_hooks_installed", new=False)
    mocker.patch.object(bot_services, "_previous_signal_handlers", new={})
    mocker.patch.object(bot_services.atexit, "register", new=lambda _fn: None)
    mocker.patch.object(bot_services.threading, "current_thread", new=lambda: object())
    mocker.patch.object(bot_services.threading, "main_thread", new=lambda: main_thread)

    def fail_signal(*_args: object) -> None:
        msg = "signal.signal must not be called outside the main thread"
        raise AssertionError(msg)

    mocker.patch.object(bot_services.signal, "signal", new=fail_signal)

    bot_services._install_shutdown_hooks()


def test_ensure_health_watchdog_reuses_existing_process(mocker) -> None:
    class ExistingProcess:
        pid = 12345

    spawned: list[object] = []

    mocker.patch.object(health_watchdog_process, "_health_proc", new=None)
    mocker.patch.object(
        health_watchdog_process,
        "existing_health_watchdog_process",
        new=lambda _repo: ExistingProcess(),
    )
    mocker.patch.object(
        health_watchdog_process.subprocess,
        "Popen",
        new=lambda *a, **kw: spawned.append((a, kw)),
    )

    health_watchdog_process.ensure_health_watchdog_process()

    assert spawned == []
    assert health_watchdog_process._health_proc is None


def test_ensure_health_watchdog_logs_existing_process_once(mocker, caplog: Any) -> None:
    class ExistingProcess:
        pid = 12345

    mocker.patch.object(health_watchdog_process, "_health_proc", new=None)
    mocker.patch.object(health_watchdog_process, "_known_health_watchdog_pid", new=None)
    mocker.patch.object(
        health_watchdog_process,
        "existing_health_watchdog_process",
        new=lambda _repo: ExistingProcess(),
    )

    with caplog.at_level(logging.INFO, logger=health_watchdog_process.__name__):
        health_watchdog_process.ensure_health_watchdog_process()
        health_watchdog_process.ensure_health_watchdog_process()

    messages = [
        record.getMessage()
        for record in caplog.records
        if "Game health watchdog subprocess already running" in record.getMessage()
    ]
    assert messages == ["Game health watchdog subprocess already running pid=12345"]


def test_stop_health_watchdog_stops_discovered_process(mocker) -> None:
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

    mocker.patch.object(health_watchdog_process, "_health_proc", new=None)
    mocker.patch.object(
        health_watchdog_process,
        "health_watchdog_processes",
        new=lambda _repo: [existing],
    )

    health_watchdog_process.stop_health_watchdog_process()

    assert existing.terminated is True
    assert existing.waited is True
    assert existing.killed is False
