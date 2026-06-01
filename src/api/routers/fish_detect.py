"""Fish-detect HTTP routes — run the Roboflow fish detector on the live frame.

Backs the Fishing Tournament debug page. Mirrors ``overlay_test`` (instance
validation + JSON result + annotated PNG endpoint), but the detector failure
path is non-fatal: the JSON endpoint returns ``available=False`` with an error
string so the dashboard degrades gracefully instead of erroring.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from api.services.fish_detect import (
    FishDetectResult,
    load_fish_detect_image,
    run_fish_detect,
)
from api.services.instances import list_instance_ids

router = APIRouter(prefix="/api", tags=["fish-detect"])


@router.get("/instances/{instance_id}/fish-detect")
def get_fish_detect(
    instance_id: str,
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
) -> FishDetectResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    return run_fish_detect(instance_id=instance_id, threshold=threshold)


@router.get("/instances/{instance_id}/fish-detect/image")
def get_fish_detect_image(
    instance_id: str,
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
) -> Response:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    png, result = load_fish_detect_image(instance_id, threshold=threshold)
    if png is None:
        raise HTTPException(
            status_code=404, detail=result.get("error") or "no rolling preview yet"
        )
    return Response(content=png, media_type="image/png")
