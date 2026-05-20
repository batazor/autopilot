"""Gift codes HTTP routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.services import gift_codes_api as svc

router = APIRouter(prefix="/api/gift-codes", tags=["gift-codes"])


@router.get("")
def get_gift_codes(q: str = Query(default="")) -> dict[str, Any]:
    return svc.build_gift_codes_view(query=q)


@router.post("/scrape")
async def post_scrape() -> dict[str, Any]:
    try:
        return await svc.scrape_gift_codes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/redeem")
async def post_redeem() -> dict[str, Any]:
    try:
        return await svc.redeem_gift_codes()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
