"""Gift codes HTTP routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.services import gift_codes_api as svc

router = APIRouter(prefix="/api/gift-codes", tags=["gift-codes"])


@router.get("")
def get_gift_codes(
    q: str = Query(default=""), game: str = Query(default="wos")
) -> dict[str, Any]:
    return svc.build_gift_codes_view(query=q, game=game)


@router.post("/scrape")
async def post_scrape(game: str = Query(default="wos")) -> dict[str, Any]:
    try:
        new = await svc.scrape_gift_codes_for_game(game)
        return {"ok": True, "game": game, "new_codes": new, "count": len(new)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/poll-status")
async def get_poll_status(game: str = Query(default="wos")) -> dict[str, Any]:
    try:
        return await svc.poll_status(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/redeem")
async def post_redeem(game: str = Query(default="wos")) -> dict[str, Any]:
    try:
        return await svc.redeem_gift_codes(game=game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# External accounts (gift_codes.external_accounts)
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


class DiscordConfigIn(BaseModel):
    bot_token: str | None = None
    clear_token: bool = False


class ExternalAccountToggleIn(BaseModel):
    enabled: bool


@router.get("/discord-config")
def get_discord_config() -> dict[str, Any]:
    return svc.build_discord_config_view()


@router.put("/discord-config")
def put_discord_config(payload: DiscordConfigIn) -> dict[str, Any]:
    try:
        return svc.update_discord_config(
            bot_token=payload.bot_token,
            clear_token=payload.clear_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/external-accounts/{player_id}")
def delete_external_account(
    player_id: int, game: str = Query(default="wos")
) -> dict[str, Any]:
    try:
        return svc.delete_external_account(game=game, player_id=player_id)
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

    Each frame is ``data: {json}`` with type progress/done/error.
    """

    async def event_source() -> Any:
        async for event in svc.stream_external_account_redeem(player_id, game=game):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        # no-transform keeps `next start`'s gzip middleware from buffering SSE.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
