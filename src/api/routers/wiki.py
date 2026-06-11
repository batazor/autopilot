"""Wiki reference HTTP routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

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
    entity: Literal["heroes", "items"],
    scope: str = Query(default="all"),
    q: str = Query(default=""),
) -> dict[str, Any]:
    return wiki_svc.list_entity_entries(entity, scope=scope, query=q)


@router.get("/{entity}/{entity_id}")
def get_entry_detail(
    entity: Literal["heroes", "items"],
    entity_id: str,
    scope: str = Query(default="all"),
) -> dict[str, Any]:
    try:
        return wiki_svc.get_entity_detail(entity, entity_id, scope=scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Icons are static reference assets; cache hard and serve 304s on revisit so
# list pages (items has ~400 tiles) don't refetch every icon each load.
_ICON_CACHE_CONTROL = "public, max-age=86400, stale-while-revalidate=604800"


@router.get("/{entity}/{entity_id}/icon")
def get_entry_icon(
    entity: Literal["heroes", "items"],
    entity_id: str,
    if_none_match: str | None = Header(default=None),
) -> Response:
    try:
        path, mime, etag = wiki_svc.resolve_icon(entity, entity_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"ETag": etag, "Cache-Control": _ICON_CACHE_CONTROL}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=path.read_bytes(), media_type=mime, headers=headers)
