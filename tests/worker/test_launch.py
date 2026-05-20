from __future__ import annotations

import os
from unittest.mock import patch

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
