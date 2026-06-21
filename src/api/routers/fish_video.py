"""Fish-detect video validation routes — upload a clip, sample at 2 fps.

Upload starts a background job; the page polls job status and renders the
per-frame annotated PNGs. Follows the image ``Response`` pattern in
``overlay_test``.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from api.services.fish_video import (
    ALLOWED_SUFFIXES,
    MAX_UPLOAD_BYTES,
    FishVideoJob,
    delete_job,
    frame_image_path,
    get_job,
    start_video_job,
)

router = APIRouter(prefix="/api/fish-detect", tags=["fish-detect"])


@router.post("/video")
async def post_video(
    file: Annotated[UploadFile, File(description="gameplay clip (mp4/mov/…)")],
    threshold: Annotated[float, Form()] = 0.4,
    interval_ms: Annotated[int, Form()] = 500,
) -> dict[str, str]:
    if not 0.0 <= threshold <= 1.0:
        raise HTTPException(status_code=422, detail="threshold must be in [0, 1]")
    if interval_ms < 100:
        raise HTTPException(status_code=422, detail="interval_ms must be >= 100")

    suffix = PurePosixPath(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if not (content_type.startswith("video/") or suffix in ALLOWED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"not a video file: {file.filename!r}")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"video too large (max {mb} MB)")
    if not content:
        raise HTTPException(status_code=422, detail="empty upload")

    job_id = start_video_job(
        content=content, suffix=suffix, threshold=threshold, interval_ms=interval_ms
    )
    return {"job_id": job_id}


@router.get("/video/{job_id}")
def get_video_job(job_id: str) -> FishVideoJob:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.get("/video/{job_id}/frame/{index}/image")
def get_video_frame_image(job_id: str, index: int) -> Response:
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    path = frame_image_path(job_id, index)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frame not ready")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.delete("/video/{job_id}")
def delete_video_job(job_id: str) -> dict[str, bool]:
    return {"ok": delete_job(job_id)}
