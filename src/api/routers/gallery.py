"""Gallery routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.services import gallery_api as svc
from api.services.game_resolver import request_game

# ``request_game`` populates the per-request game context var from ``?game=``
# (or ``?instance_id=``) so the service layer lists only the active game's
# module reference trees. See ``api.services.game_resolver``.
router = APIRouter(
    prefix="/api/gallery",
    tags=["gallery"],
    dependencies=[Depends(request_game)],
)


@router.get("")
def list_gallery(
    scope: str = Query(default="all"),
    q: str = Query(default=""),
) -> dict[str, object]:
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
