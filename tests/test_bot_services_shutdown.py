from __future__ import annotations

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
