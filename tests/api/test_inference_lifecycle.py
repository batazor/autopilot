"""Tests for the inference sidecar lifecycle service (phase logic + control).

Docker is never actually invoked — the thin ``_docker`` / ``_docker_available``
/ ``_inspect_container`` helpers are monkeypatched so the pure phase-derivation
and control flow are exercised deterministically.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.services import inference_lifecycle as il
from api.services.inference_lifecycle import _Container, _Job

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reset the module-global job and redirect the lifecycle log to tmp."""
    monkeypatch.setattr(il, "_JOB", None, raising=False)
    monkeypatch.setattr(il, "repo_root", lambda: tmp_path)


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.parametrize(
    ("image_present", "container", "job", "expected"),
    [
        (True, _Container(True, "running", "healthy", 0), None, "ready"),
        (True, _Container(True, "running", "starting", 0), None, "starting"),
        (True, _Container(True, "running", "unhealthy", 0), None, "unhealthy"),
        (True, _Container(True, "running", "none", 0), None, "starting"),
        (True, _Container(True, "exited", "none", 0), None, "stopped"),
        (True, _Container(True, "exited", "none", 1), None, "error"),
        (True, _Container(False, "", "none", 0), None, "stopped"),
        (False, _Container(False, "", "none", 0), None, "not_installed"),
        (False, _Container(False, "", "none", 0), _Job("pulling", 0.0), "pulling"),
        (True, _Container(False, "", "none", 0), _Job("starting", 0.0), "starting"),
        # A finished job that errored before the container came up → error.
        (
            True,
            _Container(False, "", "none", 0),
            _Job("starting", 0.0, error="boom", done=True),
            "error",
        ),
        # A finished job that errored but the container *is* running → not error.
        (
            True,
            _Container(True, "running", "healthy", 0),
            _Job("starting", 0.0, error="boom", done=True),
            "ready",
        ),
    ],
)
def test_derive_phase(
    image_present: bool,
    container: _Container,
    job: _Job | None,
    expected: str,
) -> None:
    assert il._derive_phase(image_present=image_present, container=container, job=job) == expected


def test_get_status_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(il, "_docker_available", lambda: (False, "docker CLI not found on PATH"))
    status = il.get_status()
    assert status["phase"] == "docker_unavailable"
    assert status["ready"] is False
    assert "docker" in status["error"].lower()
    # Endpoint metadata is still populated from settings.
    assert status["url"]
    assert status["model_id"]


def test_get_status_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(il, "_docker_available", lambda: (True, "27.0"))
    monkeypatch.setattr(il, "_image_present", lambda: True)
    monkeypatch.setattr(il, "_inspect_container", lambda: _Container(True, "running", "healthy", 0))
    status = il.get_status()
    assert status["phase"] == "ready"
    assert status["ready"] is True
    assert status["container_status"] == "running"
    assert status["health"] == "healthy"
    assert status["job_active"] is False


def test_start_running_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Already-running container → no docker start, no background job."""
    monkeypatch.setattr(il, "_docker_available", lambda: (True, "27"))
    monkeypatch.setattr(il, "_image_present", lambda: True)
    monkeypatch.setattr(il, "_inspect_container", lambda: _Container(True, "running", "healthy", 0))
    calls: list[list[str]] = []
    monkeypatch.setattr(il, "_docker", lambda args, **_: calls.append(args) or _Proc())
    spawned: list[bool] = []
    monkeypatch.setattr(il, "_start_worker", lambda: spawned.append(True))

    status = il.start_inference()
    assert status["phase"] == "ready"
    assert calls == []  # no docker control issued
    assert spawned == []


def test_start_quick_path_starts_stopped_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopped managed container + image present → instant ``docker start``, no pull."""
    monkeypatch.setattr(il, "_docker_available", lambda: (True, "27"))
    monkeypatch.setattr(il, "_image_present", lambda: True)
    monkeypatch.setattr(il, "_inspect_container", lambda: _Container(True, "exited", "none", 0))
    calls: list[list[str]] = []
    monkeypatch.setattr(il, "_docker", lambda args, **_: calls.append(args) or _Proc())
    spawned: list[bool] = []
    monkeypatch.setattr(il, "_start_worker", lambda: spawned.append(True))

    il.start_inference()
    assert ["start", il.CONTAINER_NAME] in calls
    assert spawned == []  # no background pull/run needed


def test_start_background_pull_when_image_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No image + no container → background job kicked off in the ``pulling`` phase."""
    monkeypatch.setattr(il, "_docker_available", lambda: (True, "27"))
    monkeypatch.setattr(il, "_image_present", lambda: False)
    monkeypatch.setattr(il, "_inspect_container", lambda: _Container(False, "", "none", 0))
    monkeypatch.setattr(il, "_docker", lambda *_a, **_k: _Proc())
    spawned: list[bool] = []
    monkeypatch.setattr(il, "_start_worker", lambda: spawned.append(True))

    status = il.start_inference()
    assert il._JOB is not None
    assert il._JOB.phase == "pulling"
    assert status["phase"] == "pulling"
    assert status["job_active"] is True


def test_stop_invokes_docker_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(il, "_docker_available", lambda: (True, "27"))
    monkeypatch.setattr(il, "_image_present", lambda: True)
    monkeypatch.setattr(il, "_inspect_container", lambda: _Container(True, "exited", "none", 0))
    calls: list[list[str]] = []
    monkeypatch.setattr(il, "_docker", lambda args, **_: calls.append(args) or _Proc())

    il.stop_inference()
    assert ["stop", il.CONTAINER_NAME] in calls


def test_run_args_mirror_compose_spec() -> None:
    """The managed ``docker run`` argv must match the Compose service contract."""
    args = il._run_args()
    assert args[0] == "run"
    assert "--name" in args and il.CONTAINER_NAME in args
    assert f"127.0.0.1:{il.HOST_PORT}:{il.CONTAINER_PORT}" in args
    assert f"{il.CACHE_VOLUME}:/tmp/cache" in args
    assert "--restart" in args and "unless-stopped" in args
    assert "--health-cmd" in args
    assert args[-1] == il.IMAGE
