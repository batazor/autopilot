"""`wos` entry point: a single Streamlit process (bot starts inside `ui/app.py`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULT_UI_PORT = "8501"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> None:
    try:
        import streamlit  # noqa: F401
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

    argv = [
        sys.executable,
        "-u",
        "-m",
        "streamlit",
        "run",
        str(repo / "ui" / "app.py"),
        "--server.headless",
        "true",
        "--server.port",
        port,
        "--browser.gatherUsageStats",
        "false",
    ]
    os.execve(sys.executable, argv, env)


if __name__ == "__main__":
    main()
