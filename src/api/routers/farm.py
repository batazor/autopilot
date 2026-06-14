"""Farm registration handoff API (R5 / owner-only).

Backs the ``/farm`` dashboard page: surfaces which generated account is filled
in the browser and awaiting the operator's captcha solve, and relays the
operator's Done/Failed verdict back to the registration process (see
``games.wos.farm.register`` + ``dashboard.farm_handoff``). Passwords are never
returned over the API.
"""
from __future__ import annotations

from typing import Annotated, Any

import redis  # noqa: TC002 — FastAPI resolves the Depends annotation at runtime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_redis
from config import farm_accounts_db
from dashboard import farm_handoff
from licensing.gate import require_tier
from licensing.models import LicenseError

router = APIRouter(prefix="/api/farm", tags=["farm"])


def _require_r5() -> None:
    """Farm is the owner-only R5 tier — 402 for anyone below it."""
    try:
        require_tier("r5")
    except LicenseError as exc:
        raise HTTPException(
            status_code=402,
            detail={"reason": "tier_too_low", "msg": str(exc)},
        ) from exc


class DoneBody(BaseModel):
    username: str
    outcome: str = "done"  # "done" | "failed"


class GenerateBody(BaseModel):
    count: int = 1
    seed: str | None = None
    server: str = "wos_beta"


class FidBody(BaseModel):
    fid: str


class BindBody(BaseModel):
    device_serial: str


_MAX_GENERATE = 50


@router.get("/registration/pending")
def get_pending(
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    _require_r5()
    return {"pending": farm_handoff.get_pending(client)}


@router.post("/registration/done")
def post_done(
    body: DoneBody,
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    _require_r5()
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    try:
        farm_handoff.signal(client, username, body.outcome)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "username": username, "outcome": body.outcome.strip().lower()}


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    _require_r5()
    return {
        "accounts": [
            {
                "username": a.username,
                "status": a.status,
                "fid": a.fid,
                "server": a.server,
                "device_serial": a.device_serial,
                "created_at": a.created_at,
                "registered_at": a.registered_at,
            }
            for a in farm_accounts_db.list_accounts(game="wos")
        ]
    }


@router.post("/generate")
def post_generate(body: GenerateBody) -> dict[str, Any]:
    _require_r5()
    from games.wos.farm import generator

    if body.count < 1 or body.count > _MAX_GENERATE:
        raise HTTPException(
            status_code=400, detail=f"count must be 1..{_MAX_GENERATE}"
        )
    created = generator.generate_and_store(
        body.count, seed=body.seed, server=body.server.strip() or "wos_beta"
    )
    return {"created": [a.username for a in created]}


@router.get("/accounts/{username}/secret")
def get_secret(username: str) -> dict[str, Any]:
    """Reveal a single account's password (owner-only, on demand — not in the list)."""
    _require_r5()
    acct = farm_accounts_db.get_account(username, game="wos")
    if acct is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {"username": acct.username, "password": acct.password}


@router.post("/accounts/{username}/fid")
def post_fid(username: str, body: FidBody) -> dict[str, Any]:
    _require_r5()
    if not farm_accounts_db.set_fid(username, body.fid, game="wos"):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}


@router.post("/accounts/{username}/bind")
def post_bind(username: str, body: BindBody) -> dict[str, Any]:
    _require_r5()
    serial = body.device_serial.strip()
    if not serial:
        raise HTTPException(status_code=400, detail="device_serial required")
    if not farm_accounts_db.bind_device(username, serial, game="wos"):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}


@router.delete("/accounts/{username}")
def delete_account(username: str) -> dict[str, Any]:
    _require_r5()
    if not farm_accounts_db.delete_account(username, game="wos"):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}
