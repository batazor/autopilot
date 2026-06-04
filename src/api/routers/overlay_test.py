"""Overlay-test HTTP routes — "what does the bot currently see?"."""
from __future__ import annotations

from typing import Annotated

import redis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from api.deps import get_redis
from api.services.instances import list_instance_ids
from api.services.overlay_test import (
    AreaRegionProbeResult,
    OverlayTestResult,
    RegionOcrResult,
    RegionOcrTestResult,
    ScreenDetectResult,
    load_overlay_test_image,
    run_area_region_probe,
    run_overlay_test,
    run_region_ocr,
    run_region_ocr_test,
    run_screen_detect,
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


@router.get("/instances/{instance_id}/screen-detect")
def get_screen_detect(
    instance_id: str,
    client: RedisDep,
    preview_source: str = Query(default="live", alias="previewSource"),
    preview_rel: str | None = Query(default=None, alias="previewRel"),
) -> ScreenDetectResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return run_screen_detect(
        instance_id=instance_id,
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
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
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


@router.get("/instances/{instance_id}/region-ocr")
def get_region_ocr(
    instance_id: str,
    client: RedisDep,
    regions: str = Query(..., description="comma-separated area region names"),
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
) -> RegionOcrResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    region_names = [r.strip() for r in regions.split(",") if r.strip()]
    if not region_names:
        raise HTTPException(status_code=422, detail="no region names provided")
    return run_region_ocr(
        client=client,
        instance_id=instance_id,
        regions=region_names,
        threshold=threshold,
    )


@router.post("/instances/{instance_id}/region-ocr-test")
async def post_region_ocr_test(
    instance_id: str,
    client: RedisDep,
    file: Annotated[UploadFile, File(description="custom test image (png/jpg/…)")],
    regions: Annotated[str, Form(description="comma-separated area region names")],
    threshold: Annotated[float | None, Form()] = None,
) -> RegionOcrTestResult:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    region_names = [r.strip() for r in regions.split(",") if r.strip()]
    if not region_names:
        raise HTTPException(status_code=422, detail="no region names provided")
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="file must be an image")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty file")
    # The service runs OCR via ``asyncio.run`` internally, so offload the sync
    # call to a worker thread rather than calling it on the event loop.
    return await run_in_threadpool(
        run_region_ocr_test,
        client=client,
        instance_id=instance_id,
        image_bytes=image_bytes,
        regions=region_names,
        threshold=threshold,
    )
