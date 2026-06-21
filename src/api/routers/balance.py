"""Balance config routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.services import balance_api as svc

router = APIRouter(prefix="/api/balance", tags=["balance"])


@router.get("")
def list_files() -> dict[str, object]:
    return {"files": svc.list_balance_files()}


@router.get("/{file_id}")
def get_file(file_id: str) -> dict[str, object]:
    try:
        return svc.read_balance_file(file_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
