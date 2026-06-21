"""Alliance daily-stats routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from config.state_sqlite import get_alliance_members, get_alliance_stats, list_alliance_names

router = APIRouter(
    prefix="/api",
    tags=["alliances"],
)


@router.get("/alliances")
def list_alliances() -> dict[str, list[str]]:
    return {"alliances": list_alliance_names()}


@router.get("/alliances/{alliance_name}/stats")
def alliance_stats(alliance_name: str) -> dict[str, Any]:
    name = alliance_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="alliance_name is required")
    data = get_alliance_stats(name)
    if not data["series"]:
        raise HTTPException(status_code=404, detail=f"no stats for alliance: {name}")
    return data


@router.get("/alliances/{alliance_name}/members")
def alliance_members(alliance_name: str) -> dict[str, Any]:
    name = alliance_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="alliance_name is required")
    data = get_alliance_members(name)
    if not data["members"]:
        raise HTTPException(status_code=404, detail=f"no members for alliance: {name}")
    return data
