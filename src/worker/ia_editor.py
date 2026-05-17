"""`wos-ia-editor` entry point: approval UI without embedded bot workers."""

from __future__ import annotations

import os
import signal
import sys
import urllib.error
import urllib.request
from typing import Any

from config.paths import repo_root

_DEFAULT_UI_PORT = "8502"
_STOP_SIGNAL_COUNT = 0


def _streamlit_already_running(port: int, host: str = "127.0.0.1") -> bool:
    url = f"http://{host}:{port}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def main() -> None:
    repo = repo_root()
    os.chdir(repo)
    port = os.environ.get("WOS_IA_EDITOR_PORT", _DEFAULT_UI_PORT)
    port_int = int(port)
    if (
        os.environ.get("WOS_FORCE_RESTART", "").strip().lower()
        not in ("1", "true", "yes", "on")
        and _streamlit_already_running(port_int)
    ):
        print(
            f"WOS IA editor already running at http://127.0.0.1:{port_int} "
            "(reuse that browser tab; set WOS_FORCE_RESTART=1 to start another).",
            flush=True,
        )
        return

    root = str(repo)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("STREAMLIT_SERVER_PROMPT", "false")
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    env["WOS_DISABLE_EMBEDDED_BOT"] = "1"
    sep = os.pathsep
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (sep + prev if prev else "")
    env["WOS_STREAMLIT_PORT"] = str(port_int)
    os.environ.update(env)
    if root not in sys.path:
        sys.path.insert(0, root)

    from config.runtime_bootstrap import bootstrap_runtime_observability
    from config.startup_validation import assert_startup_configs_valid

    bootstrap_runtime_observability("ia-editor")
    assert_startup_configs_valid(repo)
    from ui.ia_preview_service import ensure_ia_preview_refresher

    ensure_ia_preview_refresher()
    try:
        from streamlit.web import bootstrap
    except ImportError as exc:
        msg = "Streamlit is required: run `uv sync` (see README), then `uv run wos-ia-editor`."
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
            server.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    app = repo / "src" / "ui" / "ia_editor_app.py"
    bootstrap.run(
        str(app),
        False,
        [],
        flag_options={"server.port": port_int},
        stop_immediately_for_testing=False,
    )
    try:
        server = bootstrap._server  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        if server is not None:
            _set_up_signal_handler(server)
    except Exception:
        pass


if __name__ == "__main__":
    main()
