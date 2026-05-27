"""Per-instance routes (detail, preview, commands)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from api.deps import get_redis
from api.services import instance_detail as detail
from api.services.dashboard_stream import instance_revision
from api.services.instances import list_instance_ids
from config.test_module import (
    get_instance_test_module,
    set_instance_test_module,
)
from dashboard.dashboard_events import publish_dashboard_event

router = APIRouter(prefix="/api/instances", tags=["instances"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


class InstanceCommandBody(BaseModel):
    cmd: Literal["pause", "resume", "restart", "switch_player", "run_task"]
    player_id: str | None = None
    task_type: str | None = None


class TestModuleBody(BaseModel):
    module: str | None = None


@router.get("")
def list_instances() -> dict[str, list[str]]:
    return {"instances": list_instance_ids()}


@router.get("/games")
def list_instance_games() -> dict[str, dict[str, str]]:
    """``{instance_id: game_id}`` for every registered instance.

    The dashboard reads this once at boot to populate the per-instance game
    badge and to seed the ``?game=`` URL param when none is provided.
    """
    from config import devices_db
    from config.games import default_game

    out: dict[str, str] = {}
    try:
        for entry in devices_db.list_devices():
            instance_id = entry.name or entry.adb_serial or ""
            if not instance_id:
                continue
            try:
                game = entry.game_for_profile()
            except Exception:
                game = default_game()
            out[instance_id] = (game or default_game()).strip()
    except Exception:
        pass
    return {"games": out}


@router.get("/{instance_id}")
def get_instance(
    instance_id: str,
    client: RedisDep,
    if_revision: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    try:
        # Cache hit is safe: instance/queue mutations publish dashboard events
        # whose hook invalidates the per-instance revision key (see
        # ``dashboard/dashboard_events.py``). Stale-detail risk is bounded by
        # ``REV_TTL_SECONDS`` in ``dashboard_rev.py`` if a producer skips the
        # publish step.
        revision = instance_revision(client, instance_id, use_cache=True)
        if if_revision and if_revision == revision:
            return {"unchanged": True, "revision": revision}
        payload = detail.build_instance_detail(client, instance_id)
        payload["revision"] = revision
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{instance_id}/preview")
def get_instance_preview(instance_id: str) -> Response:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    png, _ = detail.load_preview_png(instance_id)
    if png is None:
        raise HTTPException(status_code=404, detail="no preview image available")
    # The worker rewrites this PNG every ~1s; the dashboard pulls a fresh URL on
    # the same cadence. Tell intermediate caches and the browser not to hold a
    # copy or the UI shows a stale frame after the cache-buster lines up again.
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.post("/{instance_id}/commands")
def post_instance_command(
    instance_id: str,
    body: InstanceCommandBody,
    client: RedisDep,
) -> dict[str, bool]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    payload: dict[str, Any] = {"cmd": body.cmd}
    if body.cmd == "switch_player":
        if not body.player_id:
            raise HTTPException(status_code=400, detail="player_id required")
        payload["player_id"] = body.player_id
    elif body.cmd == "run_task":
        if not body.player_id or not body.task_type:
            raise HTTPException(status_code=400, detail="player_id and task_type required")
        payload["player_id"] = body.player_id
        payload["task_type"] = body.task_type
    try:
        detail.push_command(client, instance_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    publish_dashboard_event(
        client, topic="instance", instance_id=instance_id, reason=body.cmd
    )
    publish_dashboard_event(
        client, topic="fleet", instance_id=instance_id, reason=body.cmd
    )
    if body.cmd in ("run_task", "switch_player"):
        publish_dashboard_event(
            client, topic="queue", instance_id=instance_id, reason=body.cmd
        )
    return {"ok": True}


@router.get("/{instance_id}/test-module")
def get_test_module(instance_id: str, client: RedisDep) -> dict[str, str]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    return {"module": get_instance_test_module(client, instance_id)}


@router.put("/{instance_id}/test-module")
def put_test_module(
    instance_id: str,
    body: TestModuleBody,
    client: RedisDep,
) -> dict[str, str]:
    if instance_id not in list_instance_ids():
        raise HTTPException(status_code=404, detail=f"unknown instance: {instance_id}")
    value = set_instance_test_module(client, instance_id, body.module)
    publish_dashboard_event(
        client, topic="instance", instance_id=instance_id, reason="test_module"
    )
    publish_dashboard_event(
        client, topic="queue", instance_id=instance_id, reason="test_module"
    )
    return {"module": value}
