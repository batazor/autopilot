"""Research tree reference (per-game YAML → Next.js /research-tree page)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.services import research_api

router = APIRouter(prefix="/api/research", tags=["research"])

alliance_tech_router = APIRouter(prefix="/api/alliance-tech", tags=["research"])


@router.get("")
def get_research() -> dict[str, Any]:
    return research_api.get_research_payload()


@alliance_tech_router.get("")
def get_alliance_tech() -> dict[str, Any]:
    return research_api.get_alliance_tech_payload()
