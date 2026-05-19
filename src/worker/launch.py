"""`play` entry point: a single Streamlit process (bot starts inside `ui/app.py`)."""

from __future__ import annotations

import os
import signal
import sys
import urllib.error
import urllib.request
from typing import Any

from config.paths import repo_root, src_root

_DEFAULT_UI_PORT = "8501"
_STOP_SIGNAL_COUNT = 0


def _streamlit_already_running(port: int, host: str = "127.0.0.1") -> bool:
    """True when an existing Streamlit server answers on ``port``."""
    url = f"http://{host}:{port}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def main() -> None:
    repo = repo_root()
    os.chdir(repo)
    port = os.environ.get("WOS_STREAMLIT_PORT", _DEFAULT_UI_PORT)
    port_int = int(port)
    if (
        os.environ.get("WOS_FORCE_RESTART", "").strip().lower()
        not in (
            "1",
            "true",
            "yes",
            "on",
        )
        and _streamlit_already_running(port_int)
    ):
        print(
            f"WOS UI already running at http://127.0.0.1:{port_int} "
            "(reuse that browser tab; set WOS_FORCE_RESTART=1 to start another).",
            flush=True,
        )
        return
    root = str(repo)
    env = os.environ.copy()
    # Line-oriented logs (worker / rolling screenshots) must appear immediately
    # when stdout is a pipe or IDE-captured stream — avoid full buffering.
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Streamlit may block on a first-run welcome prompt (email).
    # Force non-interactive mode for Cursor terminals/CI.
    env.setdefault("STREAMLIT_SERVER_PROMPT", "false")
    # Never auto-open a browser tab/window on each `uv run play` (see `.streamlit/config.toml`).
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    sep = os.pathsep
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (sep + prev if prev else "")
    os.environ.update(env)
    if root not in sys.path:
        sys.path.insert(0, root)

    from config.runtime_bootstrap import bootstrap_runtime_observability
    from config.startup_validation import assert_startup_configs_valid

    bootstrap_runtime_observability("ui")
    assert_startup_configs_valid(repo)
    try:
        from streamlit.web import bootstrap
    except ImportError as exc:
        msg = "Streamlit is required: run `uv sync` (see README), then `uv run play`."
        raise SystemExit(
            msg
        ) from exc

    def _set_up_signal_handler(server: Any) -> None:
        def signal_handler(signal_number: int, stack_frame: Any) -> None:
            del stack_frame
            global _STOP_SIGNAL_COUNT
            _STOP_SIGNAL_COUNT += 1
            if _STOP_SIGNAL_COUNT > 1:
                os._exit(128 + int(signal_number))
            try:
                from ui.bot_services import request_embedded_bot_stop

                request_embedded_bot_stop()
            finally:
                server.stop()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, signal_handler)  # type: ignore[attr-defined]
        else:
            signal.signal(signal.SIGQUIT, signal_handler)

    bootstrap._set_up_signal_handler = _set_up_signal_handler  # type: ignore[attr-defined]  # ty: ignore[invalid-assignment]
    bootstrap.run(
        str(src_root() / "ui" / "app.py"),
        False,
        [],
        {
            "server.headless": True,
            "server.port": port_int,
            "browser.gatherUsageStats": False,
        },
    )


if __name__ == "__main__":
    main()
