"""Event-calendar schedule HTTP routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from api.services import calendar_api as svc

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("")
def get_calendar(
    game: str = Query(default="wos"), days: int = Query(default=7, ge=1, le=30)
) -> dict[str, Any]:
    """Per-state event schedules read off the in-game calendar."""
    return svc.build_calendar_view(game=game, days=days)
