"""Local dev: start/stop the worker from the Next.js dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from worker import local_bot

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.get("/bot")
def get_bot_status() -> dict[str, Any]:
    return local_bot.bot_status()


@router.post("/bot/start")
def post_bot_start() -> dict[str, Any]:
    try:
        return local_bot.start_embedded_bot()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bot/stop")
def post_bot_stop() -> dict[str, Any]:
    try:
        return local_bot.stop_local_bot()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
