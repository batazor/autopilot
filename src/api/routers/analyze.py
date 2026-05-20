"""Overlay analyze audit routes."""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import analyze_api as svc

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.get("/audit")
def get_audit(scope: str = Query(default="all")) -> dict[str, object]:
    return svc.audit_scope(module_scope=scope)
