"""Inference sidecar lifecycle routes — pull / start / stop the fish detector.

Backs the Inference control widget on the Fish-detect page so the operator can
bring the optional Roboflow container up from the dashboard instead of a shell.
All endpoints degrade gracefully (``phase: "docker_unavailable"``) when Docker
is unreachable rather than raising.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services import inference_lifecycle as il

router = APIRouter(prefix="/api/inference", tags=["inference"])


@router.get("/status")
def get_inference_status() -> il.InferenceStatus:
    return il.get_status()


@router.post("/start")
def post_inference_start() -> il.InferenceStatus:
    try:
        return il.start_inference()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stop")
def post_inference_stop() -> il.InferenceStatus:
    try:
        return il.stop_inference()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/logs")
def get_inference_logs(
    tail: int = Query(default=200, ge=1, le=2000),
) -> il.InferenceLogs:
    return il.get_logs(tail=tail)
