"""Buildings reference (per-game YAML registry → Next.js /buildings page)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.services import buildings_api

router = APIRouter(prefix="/api/buildings", tags=["buildings"])


@router.get("")
def get_buildings() -> dict[str, Any]:
    return buildings_api.get_buildings_payload()
