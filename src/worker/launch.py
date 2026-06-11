"""`play` entry point: local dev stack (API + Next.js; worker optional).

By default the worker is **not** started — use the dashboard **Start bot** control.
"""

from __future__ import annotations

import contextlib
import logging
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
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from collections.abc import Callable

from config.paths import repo_root

_DEFAULT_API_PORT = 8765
_DEFAULT_WEB_PORT = 3000
_STARTUP_TIMEOUT_S = 120.0
_POLL_INTERVAL_S = 0.5
logger = logging.getLogger(__name__)
_REPO_CHILD_MODULES = frozenset(
    {
        "api.main",
        "worker.supervisor",
        "worker.game_health_watchdog",
    }
)


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


def _http_post_ok(url: str, *, timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 0) or 0) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _api_already_running(port: int, host: str = "127.0.0.1") -> bool:
    return _http_ok(f"http://{host}:{port}/health")


def _web_already_running(port: int, host: str = "127.0.0.1") -> bool:
    return _http_ok(f"http://{host}:{port}/overview")


def _port_listener_processes(port: int) -> list[psutil.Process]:
    out: list[psutil.Process] = []
    seen: set[int] = set()
    try:
        connections = psutil.net_connections(kind="tcp")
    except psutil.Error:
        connections = []
        for proc in psutil.process_iter():
            if proc.pid == os.getpid() or proc.pid in seen:
                continue
            try:
                proc_connections = proc.net_connections(kind="tcp")
            except psutil.Error:
                continue
            for conn in proc_connections:
                if conn.status != psutil.CONN_LISTEN:
                    continue
                if int(getattr(conn.laddr, "port", 0) or 0) != int(port):
                    continue
                out.append(proc)
                seen.add(proc.pid)
                break
        return out

    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or conn.pid is None:
            continue
        if int(getattr(conn.laddr, "port", 0) or 0) != int(port):
            continue
        if conn.pid == os.getpid() or conn.pid in seen:
            continue
        with contextlib.suppress(psutil.Error):
            out.append(psutil.Process(conn.pid))
            seen.add(conn.pid)
    return out


def _terminate_process(proc: psutil.Process) -> None:
    try:
        cmd = " ".join(proc.cmdline())
    except psutil.Error:
        cmd = proc.name()
    msg = f"Play stack: killing old process pid={proc.pid} cmd={cmd!r}"
    print(msg, flush=True)
    logger.warning(msg)
    if sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError, psutil.Error):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=8.0)
            return
        except psutil.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, psutil.Error):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=3.0)
            return
        except psutil.Error:
            return

    children = proc.children(recursive=True)
    proc.terminate()
    gone, alive = psutil.wait_procs([proc, *children], timeout=8.0)
    del gone
    for p in alive:
        with contextlib.suppress(psutil.Error):
            p.kill()


def _clear_port_or_fail(*, host: str, port: int, label: str) -> None:
    listeners = _port_listener_processes(port)
    if not listeners:
        return
    pids = ", ".join(str(p.pid) for p in listeners)
    msg = f"{label} port {host}:{port} is busy; killing old PID(s): {pids}"
    print(msg, flush=True)
    logger.warning(msg)
    for proc in listeners:
        _terminate_process(proc)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _port_listener_processes(port):
            return
        time.sleep(0.2)
    remaining = ", ".join(str(p.pid) for p in _port_listener_processes(port))
    msg = f"{label} port {host}:{port} is still in use after restart cleanup: {remaining}"
    raise SystemExit(msg)


def _default_build_cpus() -> int:
    """Worker cap for the play-driven Next build: gentle on memory, still quick.

    Half the cores (min 2, max 4) keeps the static-generation pass parallel
    enough for ~30 pages while leaving headroom so a worker is not OOM-killed
    when a preview dev server or other apps are also resident.
    """
    cores = os.cpu_count() or 4
    return max(2, min(4, cores // 2))


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


def _kill_pid_now(pid: int) -> None:
    if sys.platform == "win32":
        with contextlib.suppress(psutil.Error):
            root = psutil.Process(pid)
            for child in root.children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    child.kill()
            root.kill()
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGKILL)


def _kill_proc_now(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    _kill_pid_now(proc.pid)


def _is_repo_module_process(proc: psutil.Process, repo: Path) -> bool:
    try:
        if proc.pid == os.getpid():
            return False
        cmdline = proc.cmdline()
        module = ""
        for idx, arg in enumerate(cmdline):
            if arg == "-m" and idx + 1 < len(cmdline):
                module = cmdline[idx + 1]
                break
        if module not in _REPO_CHILD_MODULES:
            return False
        return Path(proc.cwd()).resolve() == repo
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
        return False


def _kill_repo_child_modules_now(repo: Path) -> None:
    for proc in psutil.process_iter():
        if not _is_repo_module_process(proc, repo):
            continue
        msg = f"Play stack: force-killing child pid={proc.pid}"
        print(msg, flush=True)
        logger.warning(msg)
        _kill_pid_now(proc.pid)


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
        self._api_base_url = ""
        self._stop_requested = False
        self._exit_code = 0

    def _track(self, label: str, proc: subprocess.Popen[bytes], *, reused: bool = False) -> None:
        self._services.append(_ManagedService(label=label, proc=proc, reused=reused))

    def _spawn_module(self, module: str, *, label: str | None = None) -> subprocess.Popen[bytes]:
        name = label or module
        proc = _popen([sys.executable, "-m", module], cwd=self._repo, env=self._env)
        self._track(name, proc)
        return proc

    def shutdown(self) -> None:
        if self._api_base_url:
            _http_post_ok(f"{self._api_base_url}/api/dev/bot/stop")
        for svc in reversed(self._services):
            if svc.reused:
                continue
            _terminate_proc(svc.proc)

    def emergency_shutdown(self) -> None:
        for svc in reversed(self._services):
            if svc.reused:
                continue
            _kill_proc_now(svc.proc)
        _kill_repo_child_modules_now(self._repo)

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

    def start_api(self, *, host: str, port: int) -> None:
        self._api_base_url = f"http://{host}:{port}"
        _clear_port_or_fail(host=host, port=port, label="API")
        self._spawn_module("api.main", label="api.main")

    def start_web(self, web_dir: Path, *, host: str, port: int) -> None:
        _clear_port_or_fail(host=host, port=port, label="Next.js")
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
        print("Building Next.js (npm run build)…", flush=True)
        # Cap build workers so the static-generation pass doesn't OOM-kill a
        # worker under memory pressure (e.g. a preview dev server also up). The
        # default is one worker per core — on a many-core box that is a lot of
        # concurrent memory. next.config.ts reads WOS_BUILD_CPUS into
        # experimental.cpus; a user-set value is respected. Build-only: not
        # forwarded to `next start`.
        build_env = dict(self._env)
        build_env.setdefault("WOS_BUILD_CPUS", str(_default_build_cpus()))
        subprocess.run(
            [npm, "run", "build"],
            cwd=str(web_dir),
            env=build_env,
            check=True,
        )
        next_bin = web_dir / "node_modules" / ".bin" / "next"
        proc = _popen(
            [str(next_bin), "start", "--port", str(port), "--hostname", host],
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
        return self._exit_code

    def install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: object) -> None:
            del _frame
            self._stop_requested = True
            self._exit_code = 128 + signum if signum != signal.SIGINT else 0
            print("Play stack: Ctrl+C received — force-killing children now.", flush=True)
            self.emergency_shutdown()
            os._exit(self._exit_code)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, _handler)  # type: ignore[attr-defined]
        else:
            signal.signal(signal.SIGQUIT, _handler)


def _run_modern_play() -> None:
    repo = repo_root()
    os.chdir(repo)
    host = os.environ.get("WOS_PLAY_HOST", "127.0.0.1")
    api_port = int(os.environ.get("WOS_API_PORT", str(_DEFAULT_API_PORT)))
    web_port = int(os.environ.get("PORT", str(_DEFAULT_WEB_PORT)))
    skip_web = _env_flag("WOS_PLAY_NO_WEB")
    skip_api = _env_flag("WOS_PLAY_NO_API")
    open_browser = _env_flag("WOS_PLAY_OPEN_BROWSER", default=False)

    from config.runtime_bootstrap import (
        bootstrap_runtime_observability,
        shutdown_runtime_observability,
    )
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
        print("Worker not started — use Start bot in the dashboard sidebar.", flush=True)

        if not skip_api:
            print(f"Starting API on http://{host}:{api_port} …", flush=True)
            stack.start_api(host=host, port=api_port)
            if not stack._wait_until(
                lambda: _api_already_running(api_port, host),
                timeout_s=_STARTUP_TIMEOUT_S,
                label="API",
            ):
                msg = f"API did not become ready at http://{host}:{api_port}/health"
                raise SystemExit(msg)

        if not skip_web:
            print(f"Starting Next.js on http://{host}:{web_port} …", flush=True)
            stack.start_web(web_dir, host=host, port=web_port)
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

        try:
            exit_code = stack.monitor_until_exit()
        except KeyboardInterrupt:
            # Second Ctrl+C during shutdown re-enters the signal handler and
            # raises to escalate. Swallow it here so the user sees a clean
            # 130 exit instead of a stack trace.
            print("Forced shutdown after second Ctrl+C.", flush=True)
            exit_code = 130
        raise SystemExit(exit_code)
    finally:
        stack.shutdown()
        shutdown_runtime_observability()


def main() -> None:
    _run_modern_play()


if __name__ == "__main__":
    main()
