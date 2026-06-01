"""Validate the fish detector against a recorded gameplay clip (operator tool).

Upload a video → sample it at a fixed cadence (default every 500 ms) → run the
Roboflow detector on each sampled frame → derive a **swipe prediction** per fish
from its motion across consecutive samples.

Processing runs in a background thread (a 1-minute clip at 2 fps is ~120 CPU
inference calls). The page polls :func:`get_job` for progress and renders the
per-frame annotated PNGs written under ``temporal/fishvid/<job_id>/``.

Swipe heuristic: a fish's motion between two samples is its **escape** vector;
the recommended **catch** swipe is the opposite direction. This is a heuristic to
validate/tune on real footage — not yet wired into gameplay.
"""
from __future__ import annotations

import asyncio
import logging
import math
import shutil
import threading
import uuid
from typing import TYPE_CHECKING, TypedDict

import cv2

from api.services.fish_common import (
    CATCH_COLOR,
    ESCAPE_COLOR,
    FishDetectionRow,
    detections_to_rows,
    draw_detections,
)
from config.loader import load_settings
from config.paths import repo_root
from inference.roboflow_client import InferenceUnavailableError, RoboflowDetector

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

logger = logging.getLogger(__name__)

# --- limits / tuning ---------------------------------------------------------
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_SAMPLED_FRAMES = 600  # safety cap: 600 samples @ 500ms = 5 min of footage
ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
_MAX_JOBS = 8  # keep the newest N jobs; older dirs are pruned

# swipe heuristic
_MATCH_MAX_DIST_PX = 160.0  # max center travel to call it the same fish
_MOTION_EPSILON_PX = 6.0  # below this the fish is "resting" → no prediction
_ARROW_LEN_PX = 60  # drawn arrow length for escape/catch vectors

_COMPASS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]


class SwipePrediction(TypedDict):
    """Predicted escape + catch swipe for one fish on a sampled frame."""

    fish_index: int
    center_x: int
    center_y: int
    escape_to_x: int
    escape_to_y: int
    catch_to_x: int
    catch_to_y: int
    escape_deg: float
    escape_compass: str
    catch_compass: str
    speed_px_s: float


class FishVideoFrame(TypedDict):
    """One sampled frame's results."""

    index: int  # 0-based sample index
    frame_pos: int  # source frame number
    t_ms: int  # timestamp in the source video
    detections: list[FishDetectionRow]
    swipes: list[SwipePrediction]


class FishVideoJob(TypedDict):
    """Background video-processing job state (polled by the UI)."""

    job_id: str
    state: str  # queued | running | done | error
    processed: int
    total: int
    fps_in: float
    duration_ms: int
    frame_width: int
    frame_height: int
    interval_ms: int
    threshold: float
    model_id: str
    available: bool
    error: str
    frames: list[FishVideoFrame]


_JOBS: dict[str, FishVideoJob] = {}
_JOBS_LOCK = threading.Lock()


# --- swipe heuristic (pure) --------------------------------------------------
def _compass(dx: float, dy: float) -> str:
    """8-way compass for a screen-space vector (y grows downward)."""
    deg = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
    return _COMPASS[int((deg + 22.5) % 360 // 45)]


def predict_swipes(
    prev_rows: list[FishDetectionRow],
    cur_rows: list[FishDetectionRow],
    *,
    interval_ms: int,
    max_match_dist: float = _MATCH_MAX_DIST_PX,
    motion_eps: float = _MOTION_EPSILON_PX,
    arrow_len: int = _ARROW_LEN_PX,
) -> list[SwipePrediction]:
    """Predict each fish's catch swipe from its motion since the previous sample.

    Greedy nearest-center matching between frames; a fish that moved more than
    ``motion_eps`` gets an escape vector (its travel direction) and a catch swipe
    in the opposite direction.
    """
    out: list[SwipePrediction] = []
    if not prev_rows or not cur_rows:
        return out
    used: set[int] = set()
    dt_s = max(interval_ms, 1) / 1000.0
    for i, c in enumerate(cur_rows):
        cx, cy = c["center_x"], c["center_y"]
        best_j = -1
        best_d = max_match_dist
        for j, p in enumerate(prev_rows):
            if j in used:
                continue
            d = math.hypot(cx - p["center_x"], cy - p["center_y"])
            if d < best_d:
                best_d = d
                best_j = j
        if best_j < 0:
            continue
        used.add(best_j)
        p = prev_rows[best_j]
        dx = float(cx - p["center_x"])
        dy = float(cy - p["center_y"])
        dist = math.hypot(dx, dy)
        if dist < motion_eps:
            continue
        ux, uy = dx / dist, dy / dist
        out.append(
            SwipePrediction(
                fish_index=i,
                center_x=cx,
                center_y=cy,
                escape_to_x=int(round(cx + ux * arrow_len)),
                escape_to_y=int(round(cy + uy * arrow_len)),
                catch_to_x=int(round(cx - ux * arrow_len)),
                catch_to_y=int(round(cy - uy * arrow_len)),
                escape_deg=round((math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0, 1),
                escape_compass=_compass(dx, dy),
                catch_compass=_compass(-dx, -dy),
                speed_px_s=round(dist / dt_s, 1),
            )
        )
    return out


def _draw_swipes(image_bgr: np.ndarray, swipes: list[SwipePrediction]) -> np.ndarray:
    """Draw escape (red) + catch (green) arrows over an already-boxed frame."""
    for s in swipes:
        c = (s["center_x"], s["center_y"])
        cv2.arrowedLine(image_bgr, c, (s["escape_to_x"], s["escape_to_y"]), ESCAPE_COLOR, 2, tipLength=0.3)
        cv2.arrowedLine(image_bgr, c, (s["catch_to_x"], s["catch_to_y"]), CATCH_COLOR, 2, tipLength=0.3)
    return image_bgr


# --- storage -----------------------------------------------------------------
def _jobs_root() -> Path:
    return repo_root() / "temporal" / "fishvid"


def job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def frame_image_path(job_id: str, index: int) -> Path:
    return job_dir(job_id) / f"frame_{index:04d}.png"


def _prune_old_jobs_locked() -> None:
    """Keep the newest ``_MAX_JOBS`` non-running jobs; delete the rest + dirs."""
    removable = [jid for jid, j in _JOBS.items() if j["state"] in {"done", "error"}]
    while len(_JOBS) > _MAX_JOBS and removable:
        victim = removable.pop(0)
        _JOBS.pop(victim, None)
        shutil.rmtree(job_dir(victim), ignore_errors=True)
        logger.info("fish-video: pruned old job %s", victim)


# --- worker ------------------------------------------------------------------
def _process_video(job_id: str, video_path: Path) -> None:
    job = _JOBS[job_id]
    cfg = load_settings().inference
    detector = RoboflowDetector.from_settings(cfg)
    if not detector.available():
        job["state"] = "error"
        job["error"] = "inference service not configured (set WOS_INFERENCE_URL / ROBOFLOW_API_KEY)"
        return

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        job["state"] = "error"
        job["error"] = "could not open video (unsupported codec/container?)"
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        fps = 30.0  # fall back when the container lacks an fps tag
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(fps * job["interval_ms"] / 1000.0))
    expected = min(MAX_SAMPLED_FRAMES, (total_frames + step - 1) // step) if total_frames > 0 else 0

    job["fps_in"] = round(fps, 3)
    job["duration_ms"] = int(total_frames / fps * 1000) if total_frames > 0 else 0
    job["total"] = expected
    job["state"] = "running"

    loop = asyncio.new_event_loop()
    prev_rows: list[FishDetectionRow] = []
    frame_no = -1
    sample_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % step != 0:
                continue

            if job["frame_width"] == 0:
                job["frame_height"], job["frame_width"] = int(frame.shape[0]), int(frame.shape[1])

            try:
                detections = loop.run_until_complete(
                    detector.detect(frame, threshold=job["threshold"])
                )
            except InferenceUnavailableError as exc:
                job["state"] = "error"
                job["available"] = False
                job["error"] = str(exc)
                return

            rows = detections_to_rows(detections)
            swipes = predict_swipes(prev_rows, rows, interval_ms=job["interval_ms"])
            prev_rows = rows

            annotated = draw_detections(frame, rows)
            _draw_swipes(annotated, swipes)
            out_path = frame_image_path(job_id, sample_idx)
            cv2.imwrite(str(out_path), annotated)

            job["frames"].append(
                FishVideoFrame(
                    index=sample_idx,
                    frame_pos=frame_no,
                    t_ms=int(frame_no / fps * 1000),
                    detections=rows,
                    swipes=swipes,
                )
            )
            job["processed"] = sample_idx + 1
            sample_idx += 1
            if sample_idx >= MAX_SAMPLED_FRAMES:
                logger.info("fish-video: job %s hit MAX_SAMPLED_FRAMES cap", job_id)
                break
    except Exception as exc:
        logger.exception("fish-video: job %s failed", job_id)
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        return
    finally:
        cap.release()
        loop.close()

    job["total"] = sample_idx  # exact count (in case fps/frame-count were off)
    job["state"] = "done"


# --- public API --------------------------------------------------------------
def start_video_job(*, content: bytes, suffix: str, threshold: float, interval_ms: int) -> str:
    """Persist the upload, register a job, and spawn its background worker."""
    job_id = uuid.uuid4().hex[:12]
    jdir = job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    safe_suffix = suffix if suffix in ALLOWED_SUFFIXES else ".mp4"
    video_path = jdir / f"source{safe_suffix}"
    video_path.write_bytes(content)

    cfg = load_settings().inference
    job: FishVideoJob = FishVideoJob(
        job_id=job_id,
        state="queued",
        processed=0,
        total=0,
        fps_in=0.0,
        duration_ms=0,
        frame_width=0,
        frame_height=0,
        interval_ms=interval_ms,
        threshold=round(threshold, 4),
        model_id=cfg.fish_model_id,
        available=True,
        error="",
        frames=[],
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _prune_old_jobs_locked()

    thread = threading.Thread(
        target=_process_video, args=(job_id, video_path), name=f"fishvid-{job_id}", daemon=True
    )
    thread.start()
    return job_id


def get_job(job_id: str) -> FishVideoJob | None:
    return _JOBS.get(job_id)


def delete_job(job_id: str) -> bool:
    with _JOBS_LOCK:
        existed = _JOBS.pop(job_id, None) is not None
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    return existed
