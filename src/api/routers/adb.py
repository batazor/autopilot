"""ADB / devices routes."""
from __future__ import annotations

from fastapi import APIRouter

from api.services import adb_api as svc

router = APIRouter(prefix="/api/adb", tags=["adb"])


@router.get("")
def get_status() -> dict[str, object]:
    return svc.get_adb_status()
