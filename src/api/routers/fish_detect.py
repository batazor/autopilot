"""Fish-detect HTTP routes — run the Roboflow fish detector on the live frame.

Backs the Fishing Tournament debug page. Mirrors ``overlay_test`` (instance
validation + JSON result + annotated PNG endpoint), but the detector failure
path is non-fatal: the JSON endpoint returns ``available=False`` with an error
string so the dashboard degrades gracefully instead of erroring.
"""
from __future__ import annotations

from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.deps import get_redis
from api.services.fish_detect import (
    FishDetectResult,
    load_fish_detect_image,
    run_fish_detect,
)
from api.services.fish_plan import FishPlanResult, run_fish_plan
from api.services.instances import list_instance_ids

router = APIRouter(prefix="/api", tags=["fish-detect"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/instances/{instance_id}/fish-detect")
def get_fish_detect(
    instance_id: str,
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
) -> FishDetectResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    return run_fish_detect(instance_id=instance_id, threshold=threshold)


@router.get("/instances/{instance_id}/fish-plan")
def get_fish_plan(
    instance_id: str,
    client: RedisDep,
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
    reset: bool = Query(default=False),
) -> FishPlanResult:
    """Decide phase (dodge/collect) + the steer swipe for the live frame.

    Read-only: never taps the device. Powers the ``/fish-detect`` live overlay.
    """
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    return run_fish_plan(
        client=client, instance_id=instance_id, threshold=threshold, reset=reset
    )


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
