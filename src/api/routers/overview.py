"""Overview / fleet dashboard routes."""
from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_redis
from api.services import fleet
from api.services.instances import list_instance_ids
from ui.redis_client import push_instance_command

router = APIRouter(prefix="/api", tags=["overview"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/overview")
def get_overview(client: RedisDep) -> dict[str, Any]:
    try:
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return fleet.build_overview(client)


@router.post("/instances/{instance_id}/pause-toggle")
def post_pause_toggle(instance_id: str, client: RedisDep) -> dict[str, str]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    row = fleet.build_overview(client)
    inst_row = next((r for r in row["fleet"] if r["instance_id"] == instance_id), None)
    paused = bool(inst_row and inst_row.get("paused"))
    cmd = "resume" if paused else "pause"
    push_instance_command(client, instance_id, {"cmd": cmd})
    return {"instance_id": instance_id, "cmd": cmd}
