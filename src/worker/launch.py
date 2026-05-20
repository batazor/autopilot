"""`play` entry point: local dev stack (worker + scheduler + API + Next.js).

Legacy Streamlit all-in-one UI: set ``WOS_PLAY_STREAMLIT=1`` (same as before on :8501).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 — used at runtime, not annotations-only
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from config.paths import repo_root, src_root

_DEFAULT_API_PORT = 8765
_DEFAULT_WEB_PORT = 3000
_DEFAULT_STREAMLIT_PORT = 8501
_STARTUP_TIMEOUT_S = 120.0
_POLL_INTERVAL_S = 0.5


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _http_ok(url: str, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _streamlit_already_running(port: int, host: str = "127.0.0.1") -> bool:
    return _http_ok(f"http://{host}:{port}/_stcore/health")


def _api_already_running(port: int, host: str = "127.0.0.1") -> bool:
    return _http_ok(f"http://{host}:{port}/health")


def _web_already_running(port: int, host: str = "127.0.0.1") -> bool:
    return _http_ok(f"http://{host}:{port}/overview")


def _prepare_child_env(repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    sep = os.pathsep
    root = str(repo)
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (sep + prev if prev else "")
    return env


def _popen(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
    }
    if sys.platform == "win32":
        return subprocess.Popen(args, **kwargs)  # type: ignore[arg-type]
    return subprocess.Popen(
        args,
        start_new_session=True,
        **kwargs,  # type: ignore[arg-type]
    )


def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.terminate()
        try:
            proc.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=8.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=3.0)


@dataclass
class _ManagedService:
    label: str
    proc: subprocess.Popen[bytes]
    reused: bool = False


class _PlayStack:
    def __init__(self) -> None:
        self._repo = repo_root()
        self._env = _prepare_child_env(self._repo)
        self._services: list[_ManagedService] = []
        self._stop_requested = False

    def _track(self, label: str, proc: subprocess.Popen[bytes], *, reused: bool = False) -> None:
        self._services.append(_ManagedService(label=label, proc=proc, reused=reused))

    def _spawn_module(self, module: str, *, label: str | None = None) -> subprocess.Popen[bytes]:
        name = label or module
        proc = _popen([sys.executable, "-m", module], cwd=self._repo, env=self._env)
        self._track(name, proc)
        return proc

    def shutdown(self) -> None:
        for svc in reversed(self._services):
            if svc.reused:
                continue
            _terminate_proc(svc.proc)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout_s: float,
        label: str,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._stop_requested:
            if predicate():
                return True
            if any(
                (not s.reused) and s.proc.poll() is not None
                for s in self._services
                if s.label in {"worker.supervisor", "api.main", "web"}
            ):
                dead = [
                    s.label
                    for s in self._services
                    if (not s.reused) and s.proc.poll() is not None
                ]
                print(f"Play stack: child exited before {label} was ready: {dead}", flush=True)
                return False
            time.sleep(_POLL_INTERVAL_S)
        return predicate()

    def start_bot(self) -> None:
        from ui.bot_services import ensure_health_watchdog

        self._spawn_module("worker.supervisor", label="worker.supervisor")
        ensure_health_watchdog()

    def start_api(self, *, host: str, port: int, force: bool) -> None:
        if not force and _api_already_running(port, host):
            print(
                f"API already running at http://{host}:{port} "
                "(reuse; set WOS_FORCE_RESTART=1 to start another).",
                flush=True,
            )
            return
        self._spawn_module("api.main", label="api.main")

    def start_web(self, web_dir: Path, *, host: str, port: int, force: bool) -> None:
        if not force and _web_already_running(port, host):
            print(
                f"Next.js already running at http://{host}:{port} "
                "(reuse; set WOS_FORCE_RESTART=1 to start another).",
                flush=True,
            )
            return
        npm = shutil.which("npm")
        if npm is None:
            msg = "npm not found on PATH — install Node.js 20+ to run the Next.js dashboard."
            raise SystemExit(msg)
        if not (web_dir / "node_modules").is_dir():
            print("Installing web dependencies (npm install)…", flush=True)
            subprocess.run(
                [npm, "install"],
                cwd=str(web_dir),
                env=self._env,
                check=True,
            )
        proc = _popen(
            [npm, "run", "dev"],
            cwd=web_dir,
            env=self._env,
        )
        self._track("web", proc)

    def monitor_until_exit(self) -> int:
        while not self._stop_requested:
            exited = [
                svc.label
                for svc in self._services
                if not svc.reused and svc.proc.poll() is not None
            ]
            if exited:
                print(f"Play stack: {exited[0]} exited — shutting down.", flush=True)
                return 1
            time.sleep(1.0)
        return 0

    def install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: object) -> None:
            del _frame
            self._stop_requested = True
            self.shutdown()
            raise SystemExit(128 + signum if signum != signal.SIGINT else 0)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, _handler)  # type: ignore[attr-defined]
        else:
            signal.signal(signal.SIGQUIT, _handler)


def _run_streamlit_legacy(repo: Path, port_int: int) -> None:
    """Previous ``play`` behavior — Streamlit UI with embedded worker."""
    if (
        os.environ.get("WOS_FORCE_RESTART", "").strip().lower()
        not in ("1", "true", "yes", "on")
        and _streamlit_already_running(port_int)
    ):
        print(
            f"WOS Streamlit UI already running at http://127.0.0.1:{port_int} "
            "(reuse that browser tab; set WOS_FORCE_RESTART=1 to start another).",
            flush=True,
        )
        return

    env = _prepare_child_env(repo)
    os.environ.update(env)
    root = str(repo)
    if root not in sys.path:
        sys.path.insert(0, root)

    from config.runtime_bootstrap import bootstrap_runtime_observability
    from config.startup_validation import assert_startup_configs_valid

    bootstrap_runtime_observability("ui")
    assert_startup_configs_valid(repo)
    try:
        from streamlit.web import bootstrap
    except ImportError as exc:
        msg = "Streamlit is required for WOS_PLAY_STREAMLIT=1: run `uv sync`."
        raise SystemExit(msg) from exc

    env.setdefault("STREAMLIT_SERVER_PROMPT", "false")
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    env.setdefault("STREAMLIT_SERVER_FILEWATCHERTYPE", "none")
    env.setdefault("STREAMLIT_SERVER_RUNONSAVE", "false")
    os.environ.update(env)

    _STOP_SIGNAL_COUNT = 0

    def _set_up_signal_handler(server: object) -> None:
        def signal_handler(signal_number: int, stack_frame: object) -> None:
            del stack_frame
            nonlocal _STOP_SIGNAL_COUNT
            _STOP_SIGNAL_COUNT += 1
            if _STOP_SIGNAL_COUNT > 1:
                os._exit(128 + int(signal_number))
            try:
                from ui.bot_services import request_embedded_bot_stop

                request_embedded_bot_stop()
            finally:
                server.stop()  # type: ignore[attr-defined]

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
            "server.fileWatcherType": "none",
            "server.runOnSave": False,
            "browser.gatherUsageStats": False,
        },
    )


def _run_modern_play() -> None:
    repo = repo_root()
    os.chdir(repo)
    host = os.environ.get("WOS_PLAY_HOST", "127.0.0.1")
    api_port = int(os.environ.get("WOS_API_PORT", str(_DEFAULT_API_PORT)))
    web_port = int(os.environ.get("PORT", str(_DEFAULT_WEB_PORT)))
    force = _env_flag("WOS_FORCE_RESTART")
    skip_web = _env_flag("WOS_PLAY_NO_WEB")
    skip_api = _env_flag("WOS_PLAY_NO_API")
    open_browser = _env_flag("WOS_PLAY_OPEN_BROWSER", default=True)

    from config.runtime_bootstrap import bootstrap_runtime_observability
    from config.startup_validation import assert_startup_configs_valid

    bootstrap_runtime_observability("play")
    assert_startup_configs_valid(repo)

    web_dir = repo / "web"
    if not skip_web and not web_dir.is_dir():
        msg = f"Next.js app directory not found: {web_dir}"
        raise SystemExit(msg)

    stack = _PlayStack()
    stack.install_signal_handlers()
    try:
        print("Starting worker + scheduler…", flush=True)
        stack.start_bot()

        if not skip_api:
            print(f"Starting API on http://{host}:{api_port} …", flush=True)
            stack.start_api(host=host, port=api_port, force=force)
            if not stack._wait_until(
                lambda: _api_already_running(api_port, host),
                timeout_s=_STARTUP_TIMEOUT_S,
                label="API",
            ):
                msg = f"API did not become ready at http://{host}:{api_port}/health"
                raise SystemExit(msg)

        if not skip_web:
            print(f"Starting Next.js on http://{host}:{web_port} …", flush=True)
            stack.start_web(web_dir, host=host, port=web_port, force=force)
            if not stack._wait_until(
                lambda: _web_already_running(web_port, host),
                timeout_s=_STARTUP_TIMEOUT_S,
                label="Next.js",
            ):
                msg = (
                    f"Next.js did not become ready at http://{host}:{web_port}/overview"
                )
                raise SystemExit(msg)

        overview_url = f"http://{host}:{web_port}/overview"
        api_url = f"http://{host}:{api_port}"
        print()
        print("WOS Autopilot — local dev stack", flush=True)
        print(f"  Dashboard: {overview_url}", flush=True)
        if not skip_api:
            print(f"  API:       {api_url}", flush=True)
        print("  Stop:      Ctrl+C", flush=True)
        print()

        if open_browser and not skip_web:
            webbrowser.open(overview_url)

        raise SystemExit(stack.monitor_until_exit())
    finally:
        stack.shutdown()


def main() -> None:
    if _env_flag("WOS_PLAY_STREAMLIT"):
        repo = repo_root()
        os.chdir(repo)
        port = os.environ.get("WOS_STREAMLIT_PORT", str(_DEFAULT_STREAMLIT_PORT))
        _run_streamlit_legacy(repo, int(port))
        return
    _run_modern_play()


if __name__ == "__main__":
    main()
