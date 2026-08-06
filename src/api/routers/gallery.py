"""Gallery routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.services import gallery_api as svc
from api.services.game_resolver import request_game, set_current_request_game

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.get("")
def list_gallery(
    scope: str = Query(default="all"),
    q: str = Query(default=""),
    game: str = Depends(request_game),
) -> dict[str, object]:
    # Re-pin inside the endpoint: a context var set in the (threadpool-run)
    # dependency does not propagate to the sync endpoint's thread, so the
    # service layer would otherwise fall back to the default game.
    set_current_request_game(game)
    return svc.list_gallery(scope=scope, query=q)


@router.get("/image")
def get_image(path: str = Query(..., description="Repo-relative PNG path")) -> Response:
    try:
        data = svc.read_gallery_image(path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
