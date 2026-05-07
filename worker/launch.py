"""`wos` entry point: a single Streamlit process (bot starts inside `ui/app.py`)."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any

_DEFAULT_UI_PORT = "8501"
_STOP_SIGNAL_COUNT = 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> None:
    try:
        from streamlit.web import bootstrap
    except ImportError as exc:
        raise SystemExit(
            "Streamlit is required: run `uv sync` (see README), then `uv run wos`."
        ) from exc

    repo = _repo_root()
    port = os.environ.get("WOS_STREAMLIT_PORT", _DEFAULT_UI_PORT)
    root = str(repo)
    env = os.environ.copy()
    # Line-oriented logs (worker / rolling screenshots) must appear immediately
    # when stdout is a pipe or IDE-captured stream — avoid full buffering.
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Streamlit may block on a first-run welcome prompt (email).
    # Force non-interactive mode for Cursor terminals/CI.
    env.setdefault("STREAMLIT_SERVER_PROMPT", "false")
    sep = os.pathsep
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (sep + prev if prev else "")
    os.environ.update(env)
    if root not in sys.path:
        sys.path.insert(0, root)

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

    bootstrap._set_up_signal_handler = _set_up_signal_handler  # type: ignore[attr-defined]
    bootstrap.run(
        str(repo / "ui" / "app.py"),
        False,
        [],
        {
            "server.headless": True,
            "server.port": int(port),
            "browser.gatherUsageStats": False,
        },
    )


if __name__ == "__main__":
    main()
