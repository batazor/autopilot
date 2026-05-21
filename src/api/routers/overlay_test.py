"""Overlay-test HTTP routes — "what does the bot currently see?"."""
from __future__ import annotations

from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.deps import get_redis
from api.services.instances import list_instance_ids
from api.services.overlay_test import (
    AreaRegionProbeResult,
    OverlayTestResult,
    load_overlay_test_image,
    run_area_region_probe,
    run_overlay_test,
)

router = APIRouter(prefix="/api", tags=["overlay-test"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/instances/{instance_id}/overlay-test")
def get_overlay_test(
    instance_id: str,
    client: RedisDep,
    only_current_screen: bool = Query(default=False, alias="onlyCurrentScreen"),
    ignore_screen_gate: bool = Query(default=False, alias="ignoreScreenGate"),
    has_active_player: bool = Query(default=True, alias="hasActivePlayer"),
    detailed_analysis: bool = Query(default=False, alias="detailedAnalysis"),
    preview_source: str = Query(default="live", alias="previewSource"),
    preview_rel: str | None = Query(default=None, alias="previewRel"),
) -> OverlayTestResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return run_overlay_test(
        instance_id=instance_id,
        only_current_screen=only_current_screen,
        ignore_screen_gate=ignore_screen_gate,
        has_active_player=has_active_player,
        detailed_analysis=detailed_analysis,
        preview_source=preview_source,
        preview_rel=preview_rel,
        client=client,
    )


@router.get("/instances/{instance_id}/overlay-test/image")
def get_overlay_test_image(
    instance_id: str,
    preview_source: str = Query(default="live", alias="previewSource"),
    preview_rel: str | None = Query(default=None, alias="previewRel"),
) -> Response:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    png, _, _ = load_overlay_test_image(
        instance_id,
        preview_source=preview_source,
        preview_rel=preview_rel,
    )
    if png is None:
        detail = (
            "reference image not found"
            if (preview_source or "").strip().lower() == "reference"
            else "no rolling preview yet"
        )
        raise HTTPException(status_code=404, detail=detail)
    return Response(content=png, media_type="image/png")


@router.get("/instances/{instance_id}/area-region-probe")
def get_area_region_probe(
    instance_id: str,
    client: RedisDep,
    region: str | None = Query(default=None),
    threshold: float = Query(default=0.9, ge=0.0, le=1.0),
) -> AreaRegionProbeResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return run_area_region_probe(
        client=client,
        instance_id=instance_id,
        region=region,
        threshold=threshold,
    )
