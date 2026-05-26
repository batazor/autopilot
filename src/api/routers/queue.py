"""Queue dashboard routes."""
from __future__ import annotations

import math
import time
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_redis
from api.services import click_approval_store, queue_api
from api.services.dashboard_stream import queue_revision

router = APIRouter(prefix="/api", tags=["queue"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


class QueueRunBody(BaseModel):
    task_id: str


class QueueRemoveBody(BaseModel):
    task_ids: list[str] = Field(min_length=1)


class QueueRescheduleBody(BaseModel):
    task_id: str
    scheduled_at: float = Field(description="UNIX epoch seconds")


class QueueEnqueueBody(BaseModel):
    scenario_key: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    player_id: str = ""
    scheduled_at: float = Field(description="UNIX epoch seconds")
    priority: int = 50_000


@router.get("/queue")
def get_queue(
    client: RedisDep,
    if_revision: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    # Cache hit is safe: every queue mutation publishes a dashboard event,
    # whose ``publish_dashboard_event`` hook invalidates the cached digest
    # via ``invalidate_revision_for_topic("queue")`` (see
    # ``dashboard/dashboard_events.py``). With ``use_cache=False`` we paid
    # the full rebuild on every poll (~1s once asyncio overhead was added).
    revision = queue_revision(client, use_cache=True)
    if if_revision and if_revision == revision:
        return {"unchanged": True, "revision": revision}
    view = queue_api.build_queue_view(client)
    view["revision"] = revision
    return view


@router.post("/queue/run-now")
def post_queue_run_now(body: QueueRunBody, client: RedisDep) -> dict[str, bool]:
    ok = queue_api.run_task_now(client, body.task_id)
    return {"ok": ok}


@router.post("/queue/remove")
def post_queue_remove(body: QueueRemoveBody, client: RedisDep) -> dict[str, int]:
    removed = queue_api.remove_tasks(client, body.task_ids)
    return {"removed": removed}


@router.post("/queue/reschedule")
def post_queue_reschedule(
    body: QueueRescheduleBody, client: RedisDep
) -> dict[str, bool]:
    if not math.isfinite(body.scheduled_at):
        raise HTTPException(status_code=400, detail="scheduled_at must be finite")
    # Clamp to a sane window (within ±30d of now) to prevent accidental drags.
    now = time.time()
    if abs(body.scheduled_at - now) > 30 * 24 * 3600:
        raise HTTPException(
            status_code=400, detail="scheduled_at out of allowed ±30d window"
        )
    ok = queue_api.reschedule_task(client, body.task_id, body.scheduled_at)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": ok}


@router.post("/queue/enqueue")
def post_queue_enqueue(body: QueueEnqueueBody, client: RedisDep) -> dict[str, Any]:
    if not math.isfinite(body.scheduled_at):
        raise HTTPException(status_code=400, detail="scheduled_at must be finite")
    now = time.time()
    if abs(body.scheduled_at - now) > 30 * 24 * 3600:
        raise HTTPException(
            status_code=400, detail="scheduled_at out of allowed ±30d window"
        )
    from api.services.instances import list_instance_ids

    if body.instance_id not in list_instance_ids():
        raise HTTPException(
            status_code=404, detail=f"unknown instance: {body.instance_id}"
        )
    try:
        result = queue_api.enqueue_user_task(
            client,
            scenario_key=body.scenario_key,
            instance_id=body.instance_id,
            player_id=body.player_id,
            scheduled_at=body.scheduled_at,
            priority=body.priority,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/queue/clear-all")
def post_queue_clear_all(client: RedisDep) -> dict[str, int]:
    """Wipe all pending queues (keeps ``:running`` entries intact).

    Same affordance as the Streamlit click-approvals "Clear queue" button —
    operator use case is resetting after a wedged bot state.
    """
    removed = click_approval_store.clear_queue_all(client)
    return {"removed": removed}
