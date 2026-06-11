"""Gift codes HTTP routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.services import gift_codes_api as svc
from licensing.models import LicenseError

router = APIRouter(prefix="/api/gift-codes", tags=["gift-codes"])


@router.get("")
def get_gift_codes(
    q: str = Query(default=""), game: str = Query(default="wos")
) -> dict[str, Any]:
    return svc.build_gift_codes_view(query=q, game=game)


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


# ---------------------------------------------------------------------------
# External accounts (Pro feature: gift_codes.external_accounts)
#
# Reads are always allowed (so a downgraded license still shows the existing
# table); writes require the feature flag and return 402 Payment Required
# with reason='feature_not_licensed' otherwise.
# ---------------------------------------------------------------------------


class ExternalAccountIn(BaseModel):
    player_id: int
    nickname: str | None = None
    label: str | None = None
    enabled: bool | None = None
    # Hit ``/api/player`` to confirm the fid exists in this game and to
    # auto-populate ``nickname`` if absent. Default True; pass False from
    # bulk-import flows that have already pre-validated the IDs.
    validate_fid: bool = True


class ExternalAccountToggleIn(BaseModel):
    enabled: bool


@router.get("/external-accounts")
def list_external_accounts(game: str = Query(default="wos")) -> dict[str, Any]:
    return svc.list_external_accounts(game=game)


@router.post("/external-accounts")
async def upsert_external_account(
    payload: ExternalAccountIn, game: str = Query(default="wos")
) -> dict[str, Any]:
    try:
        return await svc.upsert_external_account(
            game=game,
            player_id=payload.player_id,
            nickname=payload.nickname,
            label=payload.label,
            enabled=payload.enabled,
            validate_fid=payload.validate_fid,
        )
    except LicenseError as exc:
        raise HTTPException(
            status_code=402, detail={"reason": "feature_not_licensed", "msg": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/external-accounts/{player_id}")
def toggle_external_account(
    player_id: int,
    payload: ExternalAccountToggleIn,
    game: str = Query(default="wos"),
) -> dict[str, Any]:
    try:
        return svc.toggle_external_account(
            game=game, player_id=player_id, enabled=payload.enabled
        )
    except LicenseError as exc:
        raise HTTPException(
            status_code=402, detail={"reason": "feature_not_licensed", "msg": str(exc)}
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/external-accounts/{player_id}")
def delete_external_account(
    player_id: int, game: str = Query(default="wos")
) -> dict[str, Any]:
    try:
        return svc.delete_external_account(game=game, player_id=player_id)
    except LicenseError as exc:
        raise HTTPException(
            status_code=402, detail={"reason": "feature_not_licensed", "msg": str(exc)}
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/external-accounts/{player_id}/codes")
def external_account_codes(
    player_id: int, game: str = Query(default="wos")
) -> dict[str, Any]:
    """Per-code redemption status for one external account (child table)."""
    return svc.external_account_codes(player_id, game=game)


@router.get("/external-accounts/{player_id}/redeem/stream")
async def external_account_redeem_stream(
    player_id: int, game: str = Query(default="wos")
) -> StreamingResponse:
    """Run the redeemer for one account, streaming progress as SSE.

    Feature is checked up-front so an unlicensed caller gets 402 instead of an
    event stream. Each frame is ``data: {json}`` with type progress/done/error.
    """
    try:
        svc.require_external_accounts_feature()
    except LicenseError as exc:
        raise HTTPException(
            status_code=402, detail={"reason": "feature_not_licensed", "msg": str(exc)}
        ) from exc

    async def event_source() -> Any:
        async for event in svc.stream_external_account_redeem(player_id, game=game):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        # no-transform keeps `next start`'s gzip middleware from buffering SSE.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
