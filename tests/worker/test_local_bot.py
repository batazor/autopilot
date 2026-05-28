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
            "processes": [],
        }


def test_bot_status_supervisor() -> None:
    proc = MagicMock()
    proc.pid = 4242
    proc.create_time.return_value = 1700000000.0
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[proc]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
    ):
        assert local_bot.bot_status() == {
            "running": True,
            "mode": "supervisor",
            "pid": 4242,
            "processes": [{"pid": 4242, "started_at": 1700000000.0}],
        }


def test_bot_status_supervisor_multiple_sorted_by_start() -> None:
    older = MagicMock()
    older.pid = 100
    older.create_time.return_value = 1700000000.0
    newer = MagicMock()
    newer.pid = 200
    newer.create_time.return_value = 1700001000.0
    with (
        # Intentionally pass them out of order — bot_status() should re-sort.
        patch.object(local_bot, "_supervisor_processes", return_value=[newer, older]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
    ):
        out = local_bot.bot_status()
        assert out["pid"] == 100  # oldest first
        assert [p["pid"] for p in out["processes"]] == [100, 200]


def test_bot_status_embedded() -> None:
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=True),
    ):
        assert local_bot.bot_status() == {
            "running": True,
            "mode": "embedded",
            "pid": None,
            "processes": [{"pid": None, "started_at": None}],
        }


def test_start_embedded_bot_noop_when_running() -> None:
    with (
        patch.object(local_bot, "bot_status", return_value={"running": True, "mode": "embedded", "pid": None}),
        patch("dashboard.bot_services.ensure_embedded_bot") as ensure,
    ):
        out = local_bot.start_embedded_bot()
        ensure.assert_not_called()
        assert out["running"] is True
