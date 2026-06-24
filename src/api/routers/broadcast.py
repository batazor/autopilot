"""Alliance-broadcast catalog routes (CRUD + history + event flags).

The catalog is SQLite-only and operator-edited; these endpoints back the
``/broadcast`` dashboard page. Mirrors the modules/calendar router style.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.services import broadcast_api as svc

router = APIRouter(prefix="/api/broadcast", tags=["broadcast"])


class MessageBody(BaseModel):
    id: str | None = None
    title: str
    text: str
    category: str = "custom"
    game_scope: str = "all"
    channel: str = "alliance"
    trigger_kind: str = "cron"
    cron: str = ""
    cond: str = ""
    cooldown_minutes: int = 0
    priority: int = 100
    enabled: bool = True


class EnabledBody(BaseModel):
    enabled: bool


@router.get("/messages")
def list_messages(game: str | None = Query(default=None)) -> dict[str, Any]:
    return svc.list_messages(game=game)


@router.post("/messages")
def upsert_message(body: MessageBody) -> dict[str, Any]:
    try:
        return svc.upsert_message(body.model_dump())
    except svc.BroadcastValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/messages/{message_id}/enabled")
def set_enabled(message_id: str, body: EnabledBody) -> dict[str, Any]:
    try:
        return svc.set_enabled(message_id, body.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown message: {message_id}") from exc


@router.delete("/messages/{message_id}")
def delete_message(message_id: str) -> dict[str, Any]:
    try:
        return svc.delete_message(message_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown message: {message_id}") from exc


@router.post("/seed")
def seed_defaults() -> dict[str, Any]:
    return svc.seed_defaults()


@router.get("/history")
def history(
    game: str | None = Query(default=None),
    alliance: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    return svc.history(game=game, alliance=alliance, limit=limit)


@router.get("/event-flags")
def event_flags(game: str = Query(default="wos")) -> dict[str, Any]:
    return svc.event_flags(game=game)
