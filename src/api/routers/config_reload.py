"""Reload config endpoint — drops in-process config caches in the API process.

Worker subprocesses keep their own caches; cross-process reload should be
fanned out via Redis (not implemented here).
"""
from __future__ import annotations

from fastapi import APIRouter

from config.reload import reload_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.post("/reload")
def post_reload() -> dict[str, str]:
    reload_config()
    return {"status": "ok"}
