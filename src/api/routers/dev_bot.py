"""Local dev: start/stop the worker from the Next.js dashboard."""

from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_redis
from api.services import fleet, focus
from worker import local_bot

router = APIRouter(prefix="/api/dev", tags=["dev"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


class FocusBody(BaseModel):
    scenario_key: str
    player_id: str = ""
    abort_running: bool = False


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


# ── Isolated single-instance worker (run one scenario without the full fleet) ─


@router.get("/bot/instance/{instance_id}")
def get_instance_worker_status(instance_id: str, client: RedisDep) -> dict[str, Any]:
    status = local_bot.instance_worker_status(instance_id)
    # Surface focus + supervisor-worker liveness so the UI reflects what's live
    # regardless of how the worker was started.
    status.update(focus.focus_status(client, instance_id))
    return status


@router.post("/bot/instance/{instance_id}/start")
def post_instance_worker_start(instance_id: str) -> dict[str, Any]:
    try:
        return local_bot.start_instance_worker(instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bot/instance/{instance_id}/stop")
def post_instance_worker_stop(instance_id: str) -> dict[str, Any]:
    try:
        return local_bot.stop_instance_worker(instance_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Focus mode (run ONLY one scenario; suppress all autonomous work) ──────────


@router.post("/bot/instance/{instance_id}/focus")
def post_instance_focus(
    instance_id: str, body: FocusBody, client: RedisDep
) -> dict[str, Any]:
    try:
        return focus.focus_instance(
            client,
            instance_id=instance_id,
            scenario_key=body.scenario_key,
            player_id=body.player_id,
            abort_running=body.abort_running,
        )
    except KeyError as exc:  # unknown scenario
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:  # missing player for account-level scenario, etc.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bot/instance/{instance_id}/focus/stop")
def post_instance_focus_stop(instance_id: str, client: RedisDep) -> dict[str, Any]:
    try:
        return focus.unfocus_instance(client, instance_id=instance_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
