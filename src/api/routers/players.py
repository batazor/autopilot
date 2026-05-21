"""Player state routes."""
from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_redis
from api.services import players as players_svc
from dashboard.dashboard_events import publish_dashboard_event

router = APIRouter(prefix="/api", tags=["players"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/players")
def list_players() -> dict[str, list[str]]:
    return {"players": players_svc.list_player_ids()}


@router.get("/players/state-db")
def get_state_db() -> dict[str, Any]:
    return players_svc.get_state_db_overview()


@router.get("/players/suggest")
def suggest_player(
    client: RedisDep,
    instance_id: str = Query(default="", min_length=0),
) -> dict[str, str]:
    return {"player_id": players_svc.suggest_active_player_id(client, instance_id)}


@router.get("/players/{player_id}/state")
def get_player_state(player_id: str, client: RedisDep) -> dict[str, Any]:
    ids = players_svc.list_player_ids()
    if player_id not in ids:
        raise HTTPException(status_code=404, detail=f"unknown player: {player_id}")
    return players_svc.build_player_state(client, player_id)


@router.get("/players/{player_id}/persisted")
def get_player_persisted(player_id: str) -> dict[str, Any]:
    try:
        return players_svc.get_persisted_state(player_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/players/{player_id}/stats")
def get_player_stats(player_id: str) -> dict[str, Any]:
    ids = players_svc.list_player_ids()
    if player_id not in ids:
        raise HTTPException(status_code=404, detail=f"unknown player: {player_id}")
    return players_svc.get_player_stats(player_id)


@router.post("/players/{player_id}/century-sync")
def post_century_sync(player_id: str, client: RedisDep) -> dict[str, Any]:
    result = players_svc.century_sync(player_id)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "sync failed")
    publish_dashboard_event(
        client, topic="player", player_id=player_id, reason="century_sync"
    )
    return result
