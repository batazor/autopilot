from __future__ import annotations

import os
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


def test_clear_api_port_stops_bot_and_kills_old_process(capsys) -> None:
    proc = MagicMock()
    proc.pid = 12345
    with (
        patch("worker.launch._port_listener_processes", side_effect=[[proc], []]),
        patch("worker.launch._http_post_ok", return_value=True) as post,
        patch("worker.launch._terminate_process") as terminate,
    ):
        launch._clear_port_or_fail(host="127.0.0.1", port=8765, label="API")

    post.assert_called_once_with(
        "http://127.0.0.1:8765/api/dev/bot/stop",
        timeout=5.0,
    )
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
