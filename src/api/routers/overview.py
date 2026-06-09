"""Overview / fleet dashboard routes."""
from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_redis
from api.services import attention, fleet
from api.services.instances import list_instance_ids
from dashboard.dashboard_events import publish_dashboard_event
from dashboard.redis_client import push_instance_command

router = APIRouter(prefix="/api", tags=["overview"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/overview")
def get_overview(client: RedisDep) -> dict[str, Any]:
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return fleet.build_overview(client)


@router.get("/attention")
def get_attention(client: RedisDep) -> dict[str, Any]:
    """Ranked list of fleet problems that need an operator.

    Aggregates scenario load failures, worker crashes, offline devices,
    pending click approvals, navigation errors and stuck queues into one
    actionable feed — polled by the global dashboard banner and rendered in
    full on the overview page. Redis being down surfaces via the API status
    indicator, not here.
    """
    return attention.build_attention_view(client)


@router.post("/instances/{instance_id}/pause-toggle")
def post_pause_toggle(instance_id: str, client: RedisDep) -> dict[str, str]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    row = fleet.build_overview(client)
    inst_row = next((r for r in row["fleet"] if r["instance_id"] == instance_id), None)
    paused = bool(inst_row and inst_row.get("paused"))
    cmd = "resume" if paused else "pause"
    push_instance_command(client, instance_id, {"cmd": cmd})
    publish_dashboard_event(
        client, topic="fleet", instance_id=instance_id, reason=cmd
    )
    publish_dashboard_event(
        client, topic="instance", instance_id=instance_id, reason=cmd
    )
    return {"instance_id": instance_id, "cmd": cmd}
