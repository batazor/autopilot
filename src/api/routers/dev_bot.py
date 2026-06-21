"""Local dev: start/stop the worker from the Next.js dashboard."""

from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_redis
from api.services import fleet
from worker import local_bot

router = APIRouter(prefix="/api/dev", tags=["dev"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def _fleet_bot_status(client: redis.Redis) -> dict[str, Any] | None:
    try:
        overview = fleet.build_overview(client)
    except Exception:
        return None
    active = [
        row
        for row in overview.get("fleet", [])
        if row.get("status") in {"live", "paused"}
    ]
    if not active:
        return None
    return {
        "running": True,
        "mode": "fleet",
        "pid": None,
        "processes": [{"pid": None, "started_at": None}],
        "fleet_workers": len(active),
    }


@router.get("/bot")
def get_bot_status(client: RedisDep) -> dict[str, Any]:
    status = local_bot.bot_status()
    if status.get("running"):
        return status
    return _fleet_bot_status(client) or status


@router.post("/bot/start")
def post_bot_start() -> dict[str, Any]:
    try:
        return local_bot.start_supervisor_subprocess()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bot/stop")
def post_bot_stop() -> dict[str, Any]:
    try:
        return local_bot.stop_local_bot()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
