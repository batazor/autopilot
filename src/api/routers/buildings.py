"""Buildings reference (per-game YAML registry → Next.js /buildings page)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.services import buildings_api

router = APIRouter(prefix="/api/buildings", tags=["buildings"])


@router.get("")
def get_buildings() -> dict[str, Any]:
    return buildings_api.get_buildings_payload()


@router.get("/plan")
def get_build_plan(
    player: str | None = None,
    goal: str = "furnace",
    cap: float = 30.0,
    queues: int = 2,
) -> dict[str, Any]:
    """Time-ordered build schedule for the Buildings Gantt.

    ``queues=1`` → single-queue furnace-first; ``queues>=2`` → parallel queues.
    """
    return buildings_api.get_build_plan_payload(
        player=player, goal=goal, cap=cap, queues=queues
    )
