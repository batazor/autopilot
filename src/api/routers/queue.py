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
from api.services.dashboard_fingerprints import queue_view_digest
from api.services.dashboard_rev import REV_QUEUE_KEY, get_cached_revision, store_revision

router = APIRouter(prefix="/api", tags=["queue"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]

DEFAULT_PENDING_PAGE_SIZE = 25
DEFAULT_HISTORY_PAGE_SIZE = 20
MAX_QUEUE_PAGE_SIZE = 500


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
    replace_existing: bool = False


def _page_items(
    rows: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
) -> list[dict[str, Any]]:
    start = max(0, (page - 1) * page_size)
    return rows[start : start + page_size]


def _paginate_queue_view(
    view: dict[str, Any],
    *,
    pending_page: int,
    pending_page_size: int,
    history_page: int,
    history_page_size: int,
    full: bool,
) -> dict[str, Any]:
    out = dict(view)
    pending = list(view.get("pending") or [])
    history = list(view.get("history") or [])
    out["pending_count"] = int(view.get("pending_count") or len(pending))
    out["pending_overdue_count"] = int(
        view.get("pending_overdue_count")
        or sum(1 for row in pending if row.get("overdue"))
    )
    out["history_count"] = int(view.get("history_count") or len(history))
    if full:
        out["pending"] = pending
        out["history"] = history
        out["pending_page"] = 1
        out["pending_page_size"] = max(len(pending), 1)
        out["history_page"] = 1
        out["history_page_size"] = max(len(history), 1)
        return out
    out["pending"] = _page_items(
        pending,
        page=pending_page,
        page_size=pending_page_size,
    )
    out["history"] = _page_items(
        history,
        page=history_page,
        page_size=history_page_size,
    )
    out["pending_page"] = pending_page
    out["pending_page_size"] = pending_page_size
    out["history_page"] = history_page
    out["history_page_size"] = history_page_size
    return out


@router.get("/queue")
def get_queue(
    client: RedisDep,
    if_revision: Annotated[str | None, Query()] = None,
    pending_page: Annotated[int, Query(ge=1)] = 1,
    pending_page_size: Annotated[
        int, Query(ge=1, le=MAX_QUEUE_PAGE_SIZE)
    ] = DEFAULT_PENDING_PAGE_SIZE,
    history_page: Annotated[int, Query(ge=1)] = 1,
    history_page_size: Annotated[
        int, Query(ge=1, le=MAX_QUEUE_PAGE_SIZE)
    ] = DEFAULT_HISTORY_PAGE_SIZE,
    full: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    cached_revision = get_cached_revision(client, REV_QUEUE_KEY)
    if if_revision and cached_revision and if_revision == cached_revision:
        # Cache hit is safe: every queue mutation publishes a dashboard event,
        # whose ``publish_dashboard_event`` hook invalidates this digest via
        # ``invalidate_revision_for_topic("queue")``.
        return {"unchanged": True, "revision": cached_revision}
    if cached_revision:
        cached_view = queue_api.get_cached_queue_view(cached_revision)
        if cached_view is not None:
            out = _paginate_queue_view(
                cached_view,
                pending_page=pending_page,
                pending_page_size=pending_page_size,
                history_page=history_page,
                history_page_size=history_page_size,
                full=full,
            )
            out["revision"] = cached_revision
            return out
    view = queue_api.build_queue_view(client)
    revision = queue_view_digest(view)
    store_revision(client, REV_QUEUE_KEY, revision)
    queue_api.store_cached_queue_view(revision, view)
    out = _paginate_queue_view(
        view,
        pending_page=pending_page,
        pending_page_size=pending_page_size,
        history_page=history_page,
        history_page_size=history_page_size,
        full=full,
    )
    out["revision"] = revision
    return out


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
            replace_existing=body.replace_existing,
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
