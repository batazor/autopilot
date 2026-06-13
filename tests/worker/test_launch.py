from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

from worker import launch


def test_env_flag_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WOS_PLAY_OPEN_BROWSER", None)
        assert launch._env_flag("WOS_PLAY_OPEN_BROWSER", default=True) is True
        assert launch._env_flag("WOS_PLAY_OPEN_BROWSER", default=False) is False


def test_env_flag_truthy() -> None:
    with patch.dict(os.environ, {"WOS_PLAY_NO_WEB": "1"}):
        assert launch._env_flag("WOS_PLAY_NO_WEB") is True


def test_http_ok_success() -> None:
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.status = 200
    resp.__enter__.return_value = resp
    with patch("worker.launch.urllib.request.urlopen", return_value=resp):
        assert launch._http_ok("http://127.0.0.1:8765/health") is True


def test_api_already_running() -> None:
    with patch("worker.launch._http_ok", return_value=True):
        assert launch._api_already_running(8765) is True


def test_port_listener_processes_falls_back_when_global_scan_denied() -> None:
    conn = MagicMock()
    conn.status = launch.psutil.CONN_LISTEN
    conn.laddr.port = 8765
    proc = MagicMock()
    proc.pid = 12345
    proc.net_connections.return_value = [conn]

    with (
        patch("worker.launch.psutil.net_connections", side_effect=launch.psutil.AccessDenied),
        patch("worker.launch.psutil.process_iter", return_value=[proc]),
    ):
        assert launch._port_listener_processes(8765) == [proc]


def test_clear_api_port_kills_old_process(capsys) -> None:
    proc = MagicMock()
    proc.pid = 12345
    with (
        patch("worker.launch._port_listener_processes", side_effect=[[proc], []]),
        patch("worker.launch._terminate_process") as terminate,
    ):
        launch._clear_port_or_fail(host="127.0.0.1", port=8765, label="API")

    terminate.assert_called_once_with(proc)
    assert "killing old PID(s): 12345" in capsys.readouterr().out


def test_clear_port_fails_when_old_process_survives() -> None:
    proc = MagicMock()
    proc.pid = 12345
    with (
        patch("worker.launch._port_listener_processes", return_value=[proc]),
        patch("worker.launch._http_post_ok", return_value=False),
        patch("worker.launch._terminate_process"),
        patch("worker.launch.time.sleep"),
        pytest.raises(SystemExit, match="still in use"),
    ):
        launch._clear_port_or_fail(host="127.0.0.1", port=8765, label="API")


def test_start_web_exits_cleanly_when_build_is_interrupted(tmp_path, capsys) -> None:
    web_dir = tmp_path / "web"
    (web_dir / "node_modules").mkdir(parents=True)
    stack = launch._PlayStack()

    with (
        patch("worker.launch._clear_port_or_fail"),
        patch("worker.launch.shutil.which", return_value="/usr/bin/npm"),
        patch(
            "worker.launch.subprocess.run",
            side_effect=launch.subprocess.CalledProcessError(
                130, ["/usr/bin/npm", "run", "build"]
            ),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        stack.start_web(web_dir, host="127.0.0.1", port=3000)

    assert exc_info.value.code == 130
    assert "Next.js build interrupted." in capsys.readouterr().out


def test_start_web_reports_build_failure_without_traceback(tmp_path) -> None:
    web_dir = tmp_path / "web"
    (web_dir / "node_modules").mkdir(parents=True)
    stack = launch._PlayStack()

    with (
        patch("worker.launch._clear_port_or_fail"),
        patch("worker.launch.shutil.which", return_value="/usr/bin/npm"),
        patch(
            "worker.launch.subprocess.run",
            side_effect=launch.subprocess.CalledProcessError(
                1, ["/usr/bin/npm", "run", "build"]
            ),
        ),
        pytest.raises(SystemExit, match="Next.js build failed with exit code 1."),
    ):
        stack.start_web(web_dir, host="127.0.0.1", port=3000)


def test_play_signal_handler_force_kills_and_exits() -> None:
    stack = launch._PlayStack()
    handlers: dict[int, object] = {}

    def install(sig: int, handler: object) -> None:
        handlers[int(sig)] = handler

    with (
        patch("worker.launch.signal.signal", side_effect=install),
        patch.object(stack, "emergency_shutdown") as emergency,
        patch("worker.launch.os._exit", side_effect=SystemExit) as exit_now,
    ):
        stack.install_signal_handlers()
        handler = handlers[int(signal.SIGINT)]
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGINT, None)

    emergency.assert_called_once_with()
    exit_now.assert_called_once_with(0)
