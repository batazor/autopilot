from __future__ import annotations

from unittest.mock import MagicMock, patch

from worker import local_bot


def test_bot_status_not_running() -> None:
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
    ):
        assert local_bot.bot_status() == {
            "running": False,
            "mode": None,
            "pid": None,
        }


def test_bot_status_supervisor() -> None:
    proc = MagicMock()
    proc.pid = 4242
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[proc]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
    ):
        assert local_bot.bot_status() == {
            "running": True,
            "mode": "supervisor",
            "pid": 4242,
        }


def test_bot_status_embedded() -> None:
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=True),
    ):
        assert local_bot.bot_status() == {
            "running": True,
            "mode": "embedded",
            "pid": None,
        }


def test_start_embedded_bot_noop_when_running() -> None:
    with patch.object(local_bot, "bot_status", return_value={"running": True, "mode": "embedded", "pid": None}):
        with patch("ui.bot_services.ensure_embedded_bot") as ensure:
            out = local_bot.start_embedded_bot()
            ensure.assert_not_called()
            assert out["running"] is True
