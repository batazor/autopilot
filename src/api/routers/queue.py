"""Queue dashboard routes."""
from __future__ import annotations

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


@router.get("/queue")
def get_queue(
    client: RedisDep,
    if_revision: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    revision = queue_revision(client, use_cache=False)
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


@router.post("/queue/clear-all")
def post_queue_clear_all(client: RedisDep) -> dict[str, int]:
    """Wipe all pending queues (keeps ``:running`` entries intact).

    Same affordance as the Streamlit click-approvals "Clear queue" button —
    operator use case is resetting after a wedged bot state.
    """
    removed = click_approval_store.clear_queue_all(client)
    return {"removed": removed}
