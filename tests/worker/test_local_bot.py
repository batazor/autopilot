from __future__ import annotations

from unittest.mock import MagicMock, patch

from worker import local_bot


class _Proc:
    def __init__(self, *, cmdline: list[str], cwd: str = "/repo", pid: int = 4242) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._cwd = cwd

    def oneshot(self) -> _Proc:
        return self

    def __enter__(self) -> _Proc:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def create_time(self) -> float:
        return 1700000000.0

    def cmdline(self) -> list[str]:
        return self._cmdline

    def cwd(self) -> str:
        return self._cwd


def test_bot_status_not_running() -> None:
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
        patch.object(local_bot, "_external_worker_heartbeats", return_value=[]),
    ):
        assert local_bot.bot_status() == {
            "running": False,
            "mode": None,
            "pid": None,
            "processes": [],
        }


def test_bot_status_process_scan_failure_degrades_to_not_running() -> None:
    with (
        patch.object(local_bot, "_supervisor_processes", side_effect=OSError("process table unavailable")),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
        patch.object(local_bot, "_external_worker_heartbeats", return_value=[]),
    ):
        assert local_bot.bot_status() == {
            "running": False,
            "mode": None,
            "pid": None,
            "processes": [],
        }


def test_bot_status_external_heartbeat_reports_running() -> None:
    """A worker started out-of-band (no local process) but heartbeating into
    Redis must report running — the bug behind a false ``running: false``."""
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
        patch.object(
            local_bot,
            "_external_worker_heartbeats",
            return_value=[{"instance_id": "bs1", "age_s": 0.3}],
        ),
    ):
        out = local_bot.bot_status()
        assert out["running"] is True
        assert out["mode"] == "external"
        assert out["pid"] is None
        assert out["live_instances"] == [{"instance_id": "bs1", "age_s": 0.3}]


def test_bot_status_local_process_takes_precedence_over_heartbeat() -> None:
    """A detected local supervisor short-circuits before the heartbeat scan."""
    proc = MagicMock()
    proc.pid = 4242
    proc.create_time.return_value = 1700000000.0
    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[proc]),
        patch.object(local_bot, "_embedded_thread_alive", return_value=False),
        patch.object(local_bot, "_external_worker_heartbeats") as hb,
    ):
        out = local_bot.bot_status()
        assert out["mode"] == "supervisor"
        hb.assert_not_called()


def test_external_worker_heartbeats_filters_by_age() -> None:
    """Fresh heartbeat counts; a stale one is ignored."""
    fake = MagicMock()
    fake.scan_iter.return_value = [
        "wos:instance:bs1:state",
        "wos:instance:bs2:state",
    ]
    now = local_bot.time.time()
    fake.hget.side_effect = lambda key, _field: {
        "wos:instance:bs1:state": str(now - 1.0),     # fresh
        "wos:instance:bs2:state": str(now - 999.0),   # stale
    }[key]
    with patch("dashboard.redis_client.get_redis", return_value=fake):
        live = local_bot._external_worker_heartbeats(max_age_s=15.0)
    assert [d["instance_id"] for d in live] == ["bs1"]


def test_external_worker_heartbeats_redis_down_returns_empty() -> None:
    with patch("dashboard.redis_client.get_redis", side_effect=RuntimeError("no redis")):
        assert local_bot._external_worker_heartbeats() == []


def test_supervisor_processes_process_iter_failure_returns_empty() -> None:
    with patch.object(local_bot.psutil, "process_iter", side_effect=local_bot.psutil.AccessDenied(pid=1)):
        assert local_bot._supervisor_processes("/repo") == []


def test_supervisor_process_detection_accepts_console_script_bot() -> None:
    proc = _Proc(
        cmdline=["/repo/.venv/bin/python3", "/repo/.venv/bin/bot"],
        cwd="/repo",
    )

    assert local_bot._is_repo_supervisor_process(proc, "/repo") is True


def test_supervisor_process_detection_rejects_uv_parent() -> None:
    proc = _Proc(cmdline=["uv", "run", "bot"], cwd="/repo", pid=4243)

    assert local_bot._is_repo_supervisor_process(proc, "/repo") is False


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
        patch.object(
            local_bot,
            "_local_process_status",
            return_value={"running": True, "mode": "embedded", "pid": None},
        ),
        patch("dashboard.bot_services.ensure_embedded_bot") as ensure,
        patch(
            "worker.health_watchdog_process.ensure_health_watchdog_process"
        ) as ensure_watchdog,
    ):
        out = local_bot.start_embedded_bot()
        ensure.assert_not_called()
        ensure_watchdog.assert_called_once_with()
        assert out["running"] is True


def test_start_supervisor_subprocess_noop_revives_health_watchdog() -> None:
    with (
        patch.object(
            local_bot,
            "_local_process_status",
            return_value={"running": True, "mode": "supervisor", "pid": 123},
        ),
        patch(
            "worker.health_watchdog_process.ensure_health_watchdog_process"
        ) as ensure_watchdog,
        patch.object(local_bot.subprocess, "Popen") as popen,
    ):
        out = local_bot.start_supervisor_subprocess()

    popen.assert_not_called()
    ensure_watchdog.assert_called_once_with()
    assert out["running"] is True


def test_start_supervisor_subprocess_starts_health_watchdog() -> None:
    events: list[str] = []

    class Proc:
        pid = 777

    with (
        patch.object(local_bot, "_local_process_status", return_value={"running": False}),
        patch(
            "worker.health_watchdog_process.ensure_health_watchdog_process",
            side_effect=lambda: events.append("watchdog"),
        ),
        patch.object(
            local_bot.subprocess,
            "Popen",
            side_effect=lambda *_args, **_kwargs: events.append("supervisor") or Proc(),
        ),
    ):
        out = local_bot.start_supervisor_subprocess()

    assert out == {"running": True, "mode": "supervisor", "pid": 777}
    assert events == ["watchdog", "supervisor"]


def test_stop_supervisor_subprocess_stops_health_watchdog() -> None:
    events: list[str] = []
    proc = MagicMock()
    proc.wait.side_effect = lambda timeout: events.append(f"wait:{timeout}")

    with (
        patch.object(local_bot, "_supervisor_processes", return_value=[proc]),
        patch(
            "worker.health_watchdog_process.stop_health_watchdog_process",
            side_effect=lambda: events.append("watchdog"),
        ),
        patch.object(
            local_bot,
            "bot_status",
            return_value={"running": False, "mode": None, "pid": None},
        ),
    ):
        out = local_bot.stop_supervisor_subprocess()

    proc.terminate.assert_called_once_with()
    assert out["running"] is False
    assert events == ["wait:8.0", "watchdog"]


# ── Isolated instance_runner detection + Stop bot reaping ────────────────────


def test_instance_runner_id_extracts_id() -> None:
    proc = _Proc(
        cmdline=["/repo/.venv/bin/python3", "-m", "worker.instance_runner", "bs1"],
        cwd="/repo",
    )
    assert local_bot._instance_runner_id(proc, "/repo") == "bs1"


def test_instance_runner_id_rejects_other_module() -> None:
    proc = _Proc(
        cmdline=["/repo/.venv/bin/python3", "-m", "worker.supervisor"],
        cwd="/repo",
    )
    assert local_bot._instance_runner_id(proc, "/repo") is None


def test_instance_runner_id_rejects_foreign_repo() -> None:
    proc = _Proc(
        cmdline=["/x/.venv/bin/python3", "-m", "worker.instance_runner", "bs1"],
        cwd="/other",
    )
    assert local_bot._instance_runner_id(proc, "/repo") is None


def test_all_instance_runner_processes_finds_every_runner() -> None:
    runner1 = _Proc(cmdline=["py", "-m", "worker.instance_runner", "bs1"], cwd="/repo", pid=11)
    runner2 = _Proc(cmdline=["py", "-m", "worker.instance_runner", "bs2"], cwd="/repo", pid=12)
    other = _Proc(cmdline=["py", "-m", "worker.supervisor"], cwd="/repo", pid=13)
    with patch.object(local_bot.psutil, "process_iter", return_value=[runner1, other, runner2]):
        found = local_bot._all_instance_runner_processes("/repo")
    assert sorted(iid for _p, iid in found) == ["bs1", "bs2"]


def test_stop_local_bot_reaps_isolated_workers_when_no_supervisor() -> None:
    """The bug: a fish-detect Play worker survived Stop bot. Now it's reaped."""
    runner = MagicMock()
    runner.wait.return_value = None
    with (
        patch.object(
            local_bot,
            "_local_process_status",
            return_value={"running": False, "mode": None, "pid": None, "processes": []},
        ),
        patch.object(
            local_bot, "_all_instance_runner_processes", return_value=[(runner, "bs1")]
        ),
        patch.object(local_bot, "_clear_focus") as clear_focus,
        patch.object(
            local_bot, "bot_status", return_value={"running": False, "mode": None, "pid": None}
        ),
    ):
        out = local_bot.stop_local_bot()

    runner.terminate.assert_called_once_with()
    clear_focus.assert_called_once_with("bs1")
    assert out["running"] is False


def test_stop_local_bot_stops_supervisor_and_isolated_workers() -> None:
    events: list[str] = []
    runner = MagicMock()
    runner.wait.return_value = None
    with (
        patch.object(
            local_bot,
            "_local_process_status",
            return_value={"running": True, "mode": "supervisor", "pid": 1},
        ),
        patch.object(
            local_bot, "stop_supervisor_subprocess", side_effect=lambda: events.append("supervisor")
        ),
        patch.object(
            local_bot, "_all_instance_runner_processes", return_value=[(runner, "bs1")]
        ),
        patch.object(
            local_bot, "_clear_focus", side_effect=lambda iid: events.append(f"focus:{iid}")
        ),
        patch.object(local_bot, "bot_status", return_value={"running": False}),
    ):
        local_bot.stop_local_bot()

    assert events == ["supervisor", "focus:bs1"]
    runner.terminate.assert_called_once_with()


def test_clear_focus_swallows_redis_errors() -> None:
    with patch("dashboard.redis_client.get_redis", side_effect=RuntimeError("no redis")):
        local_bot._clear_focus("bs1")  # must not raise
