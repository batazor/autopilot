"""Stopping a map-stitch scan keeps the partial grid stitchable."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from api.services import map_stitch

if TYPE_CHECKING:
    from collections.abc import Iterator


def _job(**overrides: object) -> map_stitch.MapStitchJob:
    job = map_stitch.MapStitchJob(
        job_id="j-test", state="capturing", captured=3, total=15, frames=[],
        map_ready=False, log="", error="", stop_requested=False,
        instance_id="inst", serial="serial", rows=3, cols=5, overlap=0.3,
        swipe_ms=300, settle_s=1.0, home=True,
    )
    job.update(overrides)  # type: ignore[typeddict-item]
    return job


@pytest.fixture
def registered_job() -> Iterator[map_stitch.MapStitchJob]:
    job = _job()
    map_stitch._JOBS[job["job_id"]] = job
    yield job
    map_stitch._JOBS.pop(job["job_id"], None)
    map_stitch._PROCS.pop(job["job_id"], None)


def test_stop_job_flags_and_terminates(registered_job: map_stitch.MapStitchJob) -> None:
    proc = Mock()
    map_stitch._PROCS[registered_job["job_id"]] = proc
    assert map_stitch.stop_job(registered_job["job_id"])
    assert registered_job["stop_requested"]
    proc.terminate.assert_called_once()


def test_stop_job_unknown_id() -> None:
    assert not map_stitch.stop_job("nope")


def test_stopped_capture_with_frames_stays_stitchable() -> None:
    # frames dir doesn't exist for this fake job, so the preset list survives
    job = _job(stop_requested=True, frames=["frame_0_0.png", "frame_0_1.png"])
    map_stitch._finish_capture(job, rc=-15)  # SIGTERM from stop_job
    assert job["state"] == "captured"
    assert job["error"] == ""


def test_stopped_capture_without_frames_errors() -> None:
    job = _job(stop_requested=True, frames=[])
    map_stitch._finish_capture(job, rc=-15)
    assert job["state"] == "error"
    assert "stopped" in job["error"]


def test_unstopped_failure_still_errors() -> None:
    job = _job(frames=["frame_0_0.png"])
    map_stitch._finish_capture(job, rc=1)
    assert job["state"] == "error"
