"""Click approval HTTP routes."""
from __future__ import annotations

from typing import Annotated, Any, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.deps import get_redis
from api.services import click_approval_store as store
from api.services import notifications_api
from api.services.click_approval_overlay import load_preview_bytes
from api.services.instances import list_instance_ids

router = APIRouter(prefix="/api", tags=["click-approvals"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


class DecisionBody(BaseModel):
    decision: Literal["approve", "reject", "skip"] = Field(
        description="Operator decision written to the approval response key."
    )
    request_id: str = Field(
        default="",
        description="Current approval request id; prevents stale UI decisions.",
    )


class EnabledBody(BaseModel):
    enabled: bool


@router.get("/instances/{instance_id}/click-approval")
def get_click_approval(
    instance_id: str,
    client: RedisDep,
    source: Literal["capture", "live"] = Query(default="capture"),
) -> dict[str, object]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return store.get_approval_view(client, instance_id, image_source=source)


@router.get("/instances/{instance_id}/click-approval/status")
def get_click_approval_status(
    instance_id: str,
    client: RedisDep,
) -> dict[str, object]:
    """Read-only approval status for dashboard chrome.

    Unlike the full approval view, this endpoint does not refresh the
    approval-page heartbeat.
    """
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return store.get_approval_status(client, instance_id)


@router.get("/instances/{instance_id}/click-approval/image")
def get_click_approval_image(
    instance_id: str,
    client: RedisDep,
    source: Literal["capture", "live"] = Query(default="capture"),
) -> Response:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    payload = store.get_pending(client, instance_id)
    png, _, _ = load_preview_bytes(
        instance_id=instance_id,
        payload=payload,
        source=source,
    )
    if png is None:
        raise HTTPException(status_code=404, detail="no preview image available")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.post("/instances/{instance_id}/click-approval/decision")
def post_click_approval_decision(
    instance_id: str,
    body: DecisionBody,
    client: RedisDep,
) -> dict[str, bool]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        ok = store.submit_decision(
            client,
            instance_id,
            body.decision,
            request_id=body.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="approval request changed or disappeared; refresh and try again",
        )
    return {"ok": ok}


@router.post("/instances/{instance_id}/click-approval/enabled")
def post_click_approval_enabled(
    instance_id: str,
    body: EnabledBody,
    client: RedisDep,
) -> dict[str, bool]:
    """Toggle approval mode for an instance — mirrors the Streamlit page toggle."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    store.set_approval_enabled(client, instance_id, enabled=body.enabled)
    return {"ok": True, "enabled": body.enabled}


@router.post("/instances/{instance_id}/click-approval/clear-pending")
def post_click_approval_clear_pending(
    instance_id: str,
    client: RedisDep,
) -> dict[str, bool]:
    """Cancel an in-flight approval for the instance (treated as ``reject``)."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    cleared = store.clear_pending(client, instance_id)
    return {"ok": True, "cleared": cleared}


@router.post("/instances/{instance_id}/reset-current-screen")
def post_reset_current_screen(
    instance_id: str,
    client: RedisDep,
) -> dict[str, bool]:
    """Clear ``current_screen`` in Redis so the detector re-classifies from scratch."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    store.reset_current_screen(client, instance_id)
    return {"ok": True}


@router.post("/instances/{instance_id}/reset-active-player")
def post_reset_active_player(
    instance_id: str,
    client: RedisDep,
) -> dict[str, bool]:
    """Clear the active-player binding so the identity probe re-detects the gamer id."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    store.reset_active_player(client, instance_id)
    return {"ok": True}


@router.get("/instances/{instance_id}/notifications")
def get_instance_notifications(
    instance_id: str,
    client: RedisDep,
    seen_ids: Annotated[list[str] | None, Query(alias="seen_id")] = None,
    max_age_seconds: float = Query(default=30.0, ge=0.0, le=600.0),
) -> dict[str, list[dict[str, Any]]]:
    """Worker-emitted UI toast events for the instance, oldest first."""
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    seen = set(seen_ids or [])
    items = notifications_api.list_notifications(
        client, instance_id, seen_ids=seen, max_age_seconds=max_age_seconds
    )
    return {"items": items}
