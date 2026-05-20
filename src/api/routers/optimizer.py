"""Optimizer debug routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.services import optimizer_api as svc

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])


class SolveBody(BaseModel):
    mode: Literal["production", "playground"]
    gamer_id: str | None = None
    state_flat: dict[str, Any] | None = None
    server_age_days: int = Field(default=14, ge=0, le=400)
    plan_k: int = Field(default=8, ge=1, le=20)
    profile_id: str | None = None


class CandidateActionBody(BaseModel):
    candidate_id: str
    gamer_id: str | None = None
    state_flat: dict[str, Any] | None = None
    server_age_days: int = Field(default=14, ge=0, le=400)
    profile_id: str | None = None


class QueueBody(CandidateActionBody):
    instance_id: str


class CreateScenarioBody(BaseModel):
    domain: str
    file_key: str
    template_rel: str = ""


@router.get("/meta")
def get_meta() -> dict[str, object]:
    return svc.get_meta()


@router.post("/reload-balance")
def post_reload_balance() -> dict[str, object]:
    return svc.reload_balance()


@router.get("/history")
def get_history(
    gamer_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    return svc.list_history(gamer_id=gamer_id, limit=limit)


@router.post("/solve")
def post_solve(body: SolveBody) -> dict[str, object]:
    try:
        return svc.solve(
            mode=body.mode,
            gamer_id=body.gamer_id,
            state_flat=body.state_flat,
            server_age_days=body.server_age_days,
            plan_k=body.plan_k,
            profile_id=body.profile_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dry-run")
def post_dry_run(body: CandidateActionBody) -> dict[str, object]:
    try:
        return svc.dry_run(
            gamer_id=body.gamer_id,
            state_flat=body.state_flat,
            candidate_id=body.candidate_id,
            server_age_days=body.server_age_days,
            profile_id=body.profile_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/approve")
def post_approve(body: CandidateActionBody) -> dict[str, object]:
    if not body.gamer_id:
        raise HTTPException(status_code=400, detail="gamer_id required")
    try:
        return svc.approve(
            gamer_id=body.gamer_id,
            candidate_id=body.candidate_id,
            server_age_days=body.server_age_days,
            profile_id=body.profile_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/queue")
def post_queue(body: QueueBody) -> dict[str, object]:
    if not body.gamer_id:
        raise HTTPException(status_code=400, detail="gamer_id required")
    try:
        return svc.queue_for_bot(
            instance_id=body.instance_id,
            gamer_id=body.gamer_id,
            candidate_id=body.candidate_id,
            server_age_days=body.server_age_days,
            profile_id=body.profile_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
