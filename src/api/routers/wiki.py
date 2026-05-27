"""Wiki reference HTTP routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from api.services import wiki_api as wiki_svc
from api.services.game_resolver import request_game

router = APIRouter(
    prefix="/api/wiki",
    tags=["wiki"],
    dependencies=[Depends(request_game)],
)


@router.get("/scopes")
def get_scopes() -> dict[str, list[dict[str, str]]]:
    return {"scopes": wiki_svc.list_scopes()}


@router.get("/faq")
def get_faq() -> dict[str, Any]:
    return wiki_svc.get_faq()


@router.post("/sync/{script_key}")
async def post_sync_script(script_key: str) -> StreamingResponse:
    try:
        wiki_svc.get_sync_spec(script_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        wiki_svc.stream_sync_script(script_key),
        media_type="application/x-ndjson",
    )


@router.get("/gear")
def get_gear_list() -> dict[str, Any]:
    return wiki_svc.list_gear()


@router.get("/gear/{gear_id}")
def get_gear_detail(gear_id: str) -> dict[str, Any]:
    try:
        return wiki_svc.get_gear_detail(gear_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entity}")
def list_entries(
    entity: Literal["buildings", "heroes", "items"],
    scope: str = Query(default="all"),
    q: str = Query(default=""),
) -> dict[str, Any]:
    return wiki_svc.list_entity_entries(entity, scope=scope, query=q)


@router.get("/{entity}/{entity_id}")
def get_entry_detail(
    entity: Literal["buildings", "heroes", "items"],
    entity_id: str,
    scope: str = Query(default="all"),
) -> dict[str, Any]:
    try:
        return wiki_svc.get_entity_detail(entity, entity_id, scope=scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entity}/{entity_id}/icon")
def get_entry_icon(
    entity: Literal["buildings", "heroes", "items"],
    entity_id: str,
) -> Response:
    try:
        data, mime = wiki_svc.read_icon(entity, entity_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=mime)
