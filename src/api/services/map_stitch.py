"""World-map stitcher backend (operator tool).

Drives ``tools/map_stitch/capture.py`` and ``tools/map_stitch/stitch.py`` as
**subprocesses** from a background thread. Subprocess isolation is deliberate:
capture opens its own scrcpy server/sockets for the device, and a long CPU-bound
stitch should never run on the API event loop. The page polls :func:`get_job`
for progress (parsed from the scripts' ``Capturing N/M`` stdout) and renders the
captured frames + the stitched ``map_full.png``.

Job artifacts live under ``temporal/mapstitch/<job_id>/``; "Save map" copies the
result into ``tools/map_stitch/maps/`` — the gallery shared with the CLI.

NOTE: capture grabs the device exclusively (scrcpy reaps stale servers on start),
so the bot should be stopped / the device idle before capturing.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
import uuid
from typing import TYPE_CHECKING, TypedDict

from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_JOBS = 8           # keep newest N jobs; older dirs pruned
_LOG_TAIL_CHARS = 8000  # cap retained stdout so the job dict stays small
_PROGRESS_RE = re.compile(r"Capturing (\d+)/(\d+)")
_MAX_GRID = 12          # guardrail on rows/cols


class MapStitchJob(TypedDict):
    """Background capture/stitch job state (polled by the UI)."""

    job_id: str
    state: str  # queued | capturing | captured | stitching | done | error
    captured: int
    total: int
    frames: list[str]   # frame_<r>_<c>.png filenames present on disk
    map_ready: bool
    log: str
    error: str
    # echoed params
    serial: str
    rows: int
    cols: int
    overlap: float
    swipe_ms: int
    settle_s: float
    home: bool


_JOBS: dict[str, MapStitchJob] = {}
_JOBS_LOCK = threading.Lock()


# --- storage -----------------------------------------------------------------
def _tools_dir() -> Path:
    return repo_root() / "tools" / "map_stitch"


def _jobs_root() -> Path:
    return repo_root() / "temporal" / "mapstitch"


def job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def frames_dir(job_id: str) -> Path:
    return job_dir(job_id) / "frames"


def map_path(job_id: str) -> Path:
    return job_dir(job_id) / "map_full.png"


def frame_image_path(job_id: str, name: str) -> Path:
    # Defend against path traversal: only accept bare frame_*.png basenames.
    if "/" in name or "\\" in name or not name.startswith("frame_"):
        msg = f"invalid frame name: {name!r}"
        raise ValueError(msg)
    return frames_dir(job_id) / name


def saved_maps_dir() -> Path:
    return _tools_dir() / "maps"


def saved_map_path(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name.strip()) or "map"
    return saved_maps_dir() / f"{safe}.png"


def list_saved_maps() -> list[str]:
    d = saved_maps_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.png"))


def _prune_old_jobs_locked() -> None:
    removable = [jid for jid, j in _JOBS.items() if j["state"] in {"done", "error", "captured"}]
    while len(_JOBS) > _MAX_JOBS and removable:
        victim = removable.pop(0)
        _JOBS.pop(victim, None)
        shutil.rmtree(job_dir(victim), ignore_errors=True)
        logger.info("map-stitch: pruned old job %s", victim)


# --- subprocess runners (background threads) ---------------------------------
def _refresh_frames(job: MapStitchJob) -> None:
    fdir = frames_dir(job["job_id"])
    if fdir.is_dir():
        job["frames"] = sorted(p.name for p in fdir.glob("frame_*.png"))


def _stream(job: MapStitchJob, cmd: list[str]) -> int:
    """Run cmd, streaming stdout into job['log'] + progress; return exit code."""
    logger.info("map-stitch: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        job["log"] = (job["log"] + line)[-_LOG_TAIL_CHARS:]
        m = _PROGRESS_RE.search(line)
        if m:
            job["captured"], job["total"] = int(m.group(1)), int(m.group(2))
            _refresh_frames(job)
    return proc.wait()


def _capture_worker(job_id: str) -> None:
    job = _JOBS[job_id]
    job["state"] = "capturing"
    cmd = [
        sys.executable, str(_tools_dir() / "capture.py"),
        "--serial", job["serial"],
        "--rows", str(job["rows"]),
        "--cols", str(job["cols"]),
        "--overlap", str(job["overlap"]),
        "--swipe-ms", str(job["swipe_ms"]),
        "--settle-s", str(job["settle_s"]),
        "--frames-dir", str(frames_dir(job_id)),
    ]
    if not job["home"]:
        cmd.append("--no-home")
    try:
        rc = _stream(job, cmd)
    except Exception as exc:
        logger.exception("map-stitch: capture job %s crashed", job_id)
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        return
    _refresh_frames(job)
    if rc != 0:
        job["state"] = "error"
        job["error"] = job["error"] or f"capture exited with code {rc}"
        return
    job["state"] = "captured"


def _stitch_worker(job_id: str) -> None:
    job = _JOBS[job_id]
    job["state"] = "stitching"
    cmd = [
        sys.executable, str(_tools_dir() / "stitch.py"),
        "--frames-dir", str(frames_dir(job_id)),
        "--output", str(map_path(job_id)),
    ]
    try:
        rc = _stream(job, cmd)
    except Exception as exc:
        logger.exception("map-stitch: stitch job %s crashed", job_id)
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        return
    if rc != 0 or not map_path(job_id).is_file():
        job["state"] = "error"
        job["error"] = job["error"] or f"stitch exited with code {rc}"
        return
    job["map_ready"] = True
    job["state"] = "done"


# --- public API --------------------------------------------------------------
def start_capture_job(
    *,
    serial: str,
    rows: int,
    cols: int,
    overlap: float,
    swipe_ms: int,
    settle_s: float,
    home: bool,
) -> str:
    """Register a capture job and spawn its background worker thread."""
    if not 1 <= rows <= _MAX_GRID or not 1 <= cols <= _MAX_GRID:
        msg = f"rows/cols must be in 1..{_MAX_GRID}"
        raise ValueError(msg)
    if not 0.0 <= overlap < 1.0:
        msg = "overlap must be in [0, 1)"
        raise ValueError(msg)

    job_id = uuid.uuid4().hex[:12]
    frames_dir(job_id).mkdir(parents=True, exist_ok=True)
    job: MapStitchJob = MapStitchJob(
        job_id=job_id, state="queued", captured=0, total=rows * cols, frames=[],
        map_ready=False, log="", error="",
        serial=serial, rows=rows, cols=cols, overlap=round(overlap, 3),
        swipe_ms=swipe_ms, settle_s=settle_s, home=home,
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _prune_old_jobs_locked()
    threading.Thread(
        target=_capture_worker, args=(job_id,), name=f"mapcap-{job_id}", daemon=True,
    ).start()
    return job_id


def start_stitch(job_id: str) -> bool:
    """Spawn the stitch worker for an existing job that has frames. False if not."""
    job = _JOBS.get(job_id)
    if job is None:
        return False
    _refresh_frames(job)
    if not job["frames"] or job["state"] in {"capturing", "stitching"}:
        return False
    threading.Thread(
        target=_stitch_worker, args=(job_id,), name=f"mapstitch-{job_id}", daemon=True,
    ).start()
    return True


def get_job(job_id: str) -> MapStitchJob | None:
    return _JOBS.get(job_id)


def delete_job(job_id: str) -> bool:
    with _JOBS_LOCK:
        existed = _JOBS.pop(job_id, None) is not None
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    return existed


def save_map(job_id: str, name: str) -> str:
    """Copy a finished job's map_full.png into the shared gallery; return filename."""
    src = map_path(job_id)
    if not src.is_file():
        msg = "map not ready — stitch first"
        raise ValueError(msg)
    dest = saved_map_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest.name
