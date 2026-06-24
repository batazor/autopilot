"""Lifecycle control for the optional Roboflow inference sidecar container.

The Fishing Tournament fish detector needs a self-hosted Roboflow
inference-server. It is heavy and gated behind a docker-compose profile, so it
is not running by default — which surfaces in the dashboard as "inference
unavailable". This module lets the operator pull → start → stop that container
straight from the UI instead of dropping to a shell.

It drives a **single managed container** by a fixed name through the ``docker``
CLI (the host daemon in local dev; a mounted ``/var/run/docker.sock`` in prod).
The container spec in :func:`_run_args` mirrors the ``inference`` service in
``docker-compose.yml`` — keep them in sync. The cache volume name
(``autopilot_inference_cache``) deliberately matches the volume Compose creates
for the default ``autopilot`` project, so model weights are shared whether the
container was started here or via ``docker compose``.

Phases (``phase`` in the status dict) the UI renders as a progress track::

    docker_unavailable → not_installed → pulling → starting → ready

plus ``stopped`` (image present, container not running), ``unhealthy`` (running
but the healthcheck is failing) and ``error`` (a start job or the container
exited non-zero). Reachability is reported by the container's own healthcheck,
which the managed ``docker run`` installs (mirroring Compose).

Design note: the "pulling" sub-phase and the orchestration log are process-local
(a module global guarded by a lock), so with multiple uvicorn workers only the
worker that launched the pull sees them. Everything else is derived from
``docker inspect`` / ``docker image inspect``, which every worker sees. Local
dev runs a single worker, so this is a non-issue there.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from config.paths import repo_root

logger = logging.getLogger(__name__)

# --- Managed container spec (mirror of the ``inference`` service in
# docker-compose.yml — keep in sync) ------------------------------------------
CONTAINER_NAME = "autopilot-inference"
IMAGE = "roboflow/roboflow-inference-server-cpu:latest"
HOST_PORT = 9001
CONTAINER_PORT = 9001
CACHE_VOLUME = "autopilot_inference_cache"
# Same probe Compose uses — hits the server's /info once it can serve.
_HEALTH_CMD = (
    "python -c \"import urllib.request; "
    "urllib.request.urlopen('http://127.0.0.1:9001/info')\""
)

_LOG_REL = Path("temporal") / "inference" / "lifecycle.log"


class InferenceStatus(TypedDict):
    """Payload for ``GET /api/inference/status`` (and start/stop responses)."""

    phase: str
    ready: bool
    image_present: bool
    container_exists: bool
    container_status: str
    health: str
    job_active: bool
    url: str
    model_id: str
    error: str


class InferenceLogs(TypedDict):
    lines: list[str]
    container: str


# --- Background start job (pull + run) ---------------------------------------
@dataclass
class _Job:
    phase: str  # "pulling" | "starting"
    started_at: float
    error: str | None = None
    done: bool = False


_JOB: _Job | None = None
_JOB_LOCK = threading.Lock()


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _log_path() -> Path:
    p = repo_root() / _LOG_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_log(line: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"[{_ts()}] {line.rstrip()}\n")
    except OSError:
        logger.debug("inference: failed to append lifecycle log", exc_info=True)


# --- docker CLI helpers -------------------------------------------------------
def _docker(args: list[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _docker_available() -> tuple[bool, str]:
    """``(ok, version_or_error)`` — probes the daemon, not just the CLI."""
    try:
        proc = _docker(["version", "--format", "{{.Server.Version}}"], timeout=8)
    except FileNotFoundError:
        return False, "docker CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "docker daemon did not respond"
    except OSError as exc:
        return False, f"docker invocation failed: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "docker daemon unreachable").strip()
    return True, proc.stdout.strip()


def _image_present() -> bool:
    try:
        return _docker(["image", "inspect", IMAGE], timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@dataclass
class _Container:
    exists: bool
    status: str  # running|exited|created|restarting|paused|"" (missing)
    health: str  # healthy|starting|unhealthy|none
    exit_code: int


def _inspect_container() -> _Container:
    fmt = (
        "{{.State.Status}}|"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|"
        "{{.State.ExitCode}}"
    )
    try:
        proc = _docker(["inspect", "--format", fmt, CONTAINER_NAME], timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return _Container(False, "", "none", 0)
    if proc.returncode != 0:
        return _Container(False, "", "none", 0)
    parts = [*proc.stdout.strip().split("|"), "", "none", "0"][:3]
    status, health, exit_raw = parts
    try:
        exit_code = int(exit_raw)
    except ValueError:
        exit_code = 0
    return _Container(True, status, health or "none", exit_code)


def _run_args() -> list[str]:
    """``docker run`` argv mirroring the Compose ``inference`` service."""
    api_key = os.environ.get("ROBOFLOW_API_KEY", "")
    return [
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "--label",
        "autopilot.managed=inference",
        "-p",
        f"127.0.0.1:{HOST_PORT}:{CONTAINER_PORT}",
        "-e",
        f"ROBOFLOW_API_KEY={api_key}",
        "-v",
        f"{CACHE_VOLUME}:/tmp/cache",
        "--restart",
        "unless-stopped",
        "--health-cmd",
        _HEALTH_CMD,
        "--health-interval",
        "30s",
        "--health-timeout",
        "10s",
        "--health-start-period",
        "40s",
        "--health-retries",
        "3",
        IMAGE,
    ]


def _stream_to_log(cmd: list[str], *, timeout: float = 1800.0) -> int:
    """Run ``cmd``, append combined stdout+stderr to the lifecycle log, return exit code."""
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                cmd, stdout=fh, stderr=subprocess.STDOUT, text=True
            )
            return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _append_log(f"command timed out: {' '.join(cmd)}")
        return 124
    except OSError as exc:
        _append_log(f"command failed to launch: {exc}")
        return 127


# --- phase derivation (pure — unit-tested) -----------------------------------
def _derive_phase(*, image_present: bool, container: _Container, job: _Job | None) -> str:
    # An in-flight pull/run job dictates the phase it is currently in.
    if job is not None and not job.done:
        return job.phase
    if job is not None and job.error and not (container.exists and container.status == "running"):
        return "error"

    if container.exists:
        if container.status == "running":
            if container.health in ("healthy", "none"):
                # No healthcheck info ("none") only happens for foreign containers;
                # the managed one always reports — treat a bare running as ready.
                return "ready" if container.health == "healthy" else "starting"
            if container.health == "unhealthy":
                return "unhealthy"
            return "starting"  # health == "starting"
        # Exists but not running.
        return "error" if container.exit_code != 0 else "stopped"

    # No container at all.
    return "stopped" if image_present else "not_installed"


def _current_job() -> _Job | None:
    with _JOB_LOCK:
        return _JOB


def get_status() -> InferenceStatus:
    """Compute the current lifecycle status of the managed inference container."""
    from config.loader import load_settings

    cfg = load_settings().inference
    base: InferenceStatus = {
        "phase": "docker_unavailable",
        "ready": False,
        "image_present": False,
        "container_exists": False,
        "container_status": "",
        "health": "none",
        "job_active": False,
        "url": cfg.service_url,
        "model_id": cfg.fish_model_id,
        "error": "",
    }

    ok, version_or_err = _docker_available()
    if not ok:
        base["error"] = version_or_err
        return base

    image = _image_present()
    container = _inspect_container()
    job = _current_job()
    phase = _derive_phase(image_present=image, container=container, job=job)

    base["phase"] = phase
    base["ready"] = phase == "ready"
    base["image_present"] = image
    base["container_exists"] = container.exists
    base["container_status"] = container.status
    base["health"] = container.health
    base["job_active"] = job is not None and not job.done
    if job is not None and job.error:
        base["error"] = job.error
    elif phase == "error" and container.exists:
        base["error"] = f"container exited with code {container.exit_code}"
    return base


# --- control ------------------------------------------------------------------
def _start_worker() -> None:
    """Background: pull the image if missing, then run the container."""
    global _JOB
    try:
        if not _image_present():
            _set_job_phase("pulling")
            _append_log(f"pulling {IMAGE} …")
            code = _stream_to_log(["docker", "pull", IMAGE])
            if code != 0:
                _finish_job(error=f"docker pull failed (exit {code}) — see log")
                return

        _set_job_phase("starting")
        # A stale non-running container with our name would make ``run --name``
        # clash — recreate it from the (now present) image.
        existing = _inspect_container()
        if existing.exists and existing.status != "running":
            _docker(["rm", "-f", CONTAINER_NAME], timeout=30)
        _append_log(f"starting container {CONTAINER_NAME} …")
        code = _stream_to_log(["docker", *_run_args()], timeout=120)
        if code != 0:
            _finish_job(error=f"docker run failed (exit {code}) — see log")
            return
        _append_log("container started")
        _finish_job(error=None)
    except Exception as exc:
        logger.debug("inference start worker crashed", exc_info=True)
        _finish_job(error=f"{type(exc).__name__}: {exc}")


def _set_job_phase(phase: str) -> None:
    global _JOB
    with _JOB_LOCK:
        if _JOB is not None:
            _JOB.phase = phase


def _finish_job(*, error: str | None) -> None:
    global _JOB
    with _JOB_LOCK:
        if _JOB is not None:
            _JOB.error = error
            _JOB.done = True


def start_inference() -> InferenceStatus:
    """Pull (if needed) and start the managed inference container.

    Returns immediately: a missing image is pulled on a background thread and
    the phase advances ``pulling → starting → ready``. A stopped managed
    container with the image already present is started synchronously (instant).
    """
    global _JOB

    ok, _ = _docker_available()
    if not ok:
        return get_status()

    container = _inspect_container()
    if container.exists and container.status == "running":
        return get_status()

    if container.exists and _image_present():
        # Stopped managed container + image present → instant start, no pull.
        proc = _docker(["start", CONTAINER_NAME], timeout=30)
        if proc.returncode == 0:
            _append_log(f"started existing container {CONTAINER_NAME}")
            return get_status()
        _append_log(f"docker start failed: {proc.stderr.strip()} — recreating")
        _docker(["rm", "-f", CONTAINER_NAME], timeout=30)

    with _JOB_LOCK:
        if _JOB is not None and not _JOB.done:
            return get_status()  # a job is already in flight
        _JOB = _Job(
            phase="pulling" if not _image_present() else "starting",
            started_at=time.time(),
        )
    with contextlib.suppress(OSError):
        _log_path().write_text("", encoding="utf-8")  # fresh log per run
    threading.Thread(target=_start_worker, name="inference-start", daemon=True).start()
    return get_status()


def stop_inference() -> InferenceStatus:
    """Stop the managed inference container (keeps it for an instant restart)."""
    ok, _ = _docker_available()
    if not ok:
        return get_status()
    proc = _docker(["stop", CONTAINER_NAME], timeout=40)
    if proc.returncode == 0:
        _append_log(f"stopped container {CONTAINER_NAME}")
    return get_status()


def get_logs(*, tail: int = 200) -> InferenceLogs:
    """Tail the orchestration log (pull/run output) plus the container's own logs."""
    lines: list[str] = []
    log_file = repo_root() / _LOG_REL
    if log_file.exists():
        try:
            lines.extend(
                log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
            )
        except OSError:
            logger.debug("inference: failed to read lifecycle log", exc_info=True)

    ok, _ = _docker_available()
    if ok and _inspect_container().exists:
        try:
            proc = _docker(["logs", "--tail", str(tail), CONTAINER_NAME], timeout=15)
            # The roboflow image logs to both streams — merge them.
            container_lines = ((proc.stdout or "") + (proc.stderr or "")).splitlines()
        except (subprocess.TimeoutExpired, OSError):
            container_lines = []
        if container_lines:
            lines.append("──── container logs ────")
            lines.extend(container_lines[-tail:])

    return {"lines": lines[-(tail * 2) :], "container": CONTAINER_NAME}
