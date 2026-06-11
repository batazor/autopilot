"""World-map stitcher routes — capture a grid of frames over scrcpy, then stitch.

POST /capture starts a background capture job; the page polls job status and,
once frames exist, POSTs /stitch. Image bytes are returned via the ``Response``
pattern used by ``overlay_test`` / ``fish_video``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.services.map_stitch import (
    MapStitchJob,
    delete_job,
    frame_image_path,
    get_job,
    list_saved_maps,
    map_path,
    save_map,
    saved_map_path,
    start_capture_job,
    start_stitch,
    stop_job,
)

router = APIRouter(prefix="/api/map-stitch", tags=["map-stitch"])


class CaptureBody(BaseModel):
    instance_id: str
    rows: int = Field(default=3, ge=1, le=12)
    cols: int = Field(default=5, ge=1, le=12)
    overlap: float = Field(default=0.30, ge=0.0, lt=1.0)
    swipe_ms: int = Field(default=300, ge=100, le=2000)
    settle_s: float = Field(default=1.0, ge=0.1, le=10.0)
    home: bool = True


class SaveBody(BaseModel):
    name: str = "map"


def _capture_target(body: CaptureBody) -> tuple[str, str]:
    """Return ``(serial, instance_id)`` for a capture request."""
    instance_id = body.instance_id.strip()
    if not instance_id:
        msg = "missing instance_id"
        raise ValueError(msg)
    try:
        from config.devices import load_devices

        for entry in load_devices().devices:
            aliases = {entry.name, entry.adb_serial, entry.effective_serial}
            if instance_id in aliases:
                serial = entry.effective_serial
                if serial:
                    return serial, entry.name
                break
    except Exception as exc:
        msg = f"failed to resolve instance {instance_id!r}: {exc}"
        raise ValueError(msg) from exc
    msg = f"unknown instance: {instance_id!r}"
    raise ValueError(msg)


@router.post("/capture")
def post_capture(body: CaptureBody) -> dict[str, str]:
    try:
        serial, instance_id = _capture_target(body)
        job_id = start_capture_job(
            serial=serial, instance_id=instance_id, rows=body.rows, cols=body.cols,
            overlap=body.overlap, swipe_ms=body.swipe_ms,
            settle_s=body.settle_s, home=body.home,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job_id": job_id}


@router.post("/{job_id}/stop")
def post_stop(job_id: str) -> dict[str, bool]:
    """Abort a running capture/stitch; frames already captured stay stitchable."""
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return {"ok": stop_job(job_id)}


@router.post("/{job_id}/stitch")
def post_stitch(job_id: str) -> dict[str, bool]:
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    if not start_stitch(job_id):
        raise HTTPException(status_code=409, detail="no frames yet, or job is busy")
    return {"ok": True}


@router.get("/maps")
def get_saved_maps() -> dict[str, list[str]]:
    return {"maps": list_saved_maps()}


@router.get("/maps/{name}/image")
def get_saved_map_image(name: str) -> Response:
    path = saved_map_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown map: {name}")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.get("/{job_id}")
def get_job_status(job_id: str) -> MapStitchJob:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.get("/{job_id}/frame/{name}/image")
def get_frame_image(job_id: str, name: str) -> Response:
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    try:
        path = frame_image_path(job_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frame not ready")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.get("/{job_id}/map/image")
def get_map_image(job_id: str) -> Response:
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    path = map_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="map not ready")
    return Response(
        content=path.read_bytes(), media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.post("/{job_id}/save")
def post_save(job_id: str, body: SaveBody) -> dict[str, str]:
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    try:
        name = save_map(job_id, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"name": name}


@router.delete("/{job_id}")
def delete(job_id: str) -> dict[str, bool]:
    return {"ok": delete_job(job_id)}
