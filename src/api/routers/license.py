"""License gate routes: public fingerprint/status/import + admin-only issuer."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Header, UploadFile
from pydantic import BaseModel, Field

from api.services import license_api as svc

router = APIRouter(prefix="/api/license", tags=["license"])

_MAX_UPLOAD_BYTES = 64 * 1024  # license files are <2 KB; cap any oversized upload early


class IssueRequest(BaseModel):
    sub: str = Field(..., description="user email / stable id", min_length=1)
    machine_id: str = Field(..., description="fingerprint from the user's UI", min_length=1)
    days: int = Field(30, ge=1, le=365)
    tier: str = Field("r3", min_length=1)
    features: list[str] = Field(default_factory=list)
    max_devices: int = Field(1, ge=1, le=100)
    max_players_per_device: int = Field(3, ge=1, le=100)


@router.get("/fingerprint")
def get_fingerprint() -> dict[str, Any]:
    """The host fingerprint the user copies and sends to the issuer."""
    return svc.get_fingerprint()


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Current license state + admin flag + resolved license file path."""
    return svc.get_status()


@router.get("/plans")
def get_plans() -> list[dict[str, Any]]:
    """Public plan catalog (R2/R3/R4 tiers, prices, features)."""
    return svc.list_plans()


@router.post("/issue")
def post_issue(
    body: IssueRequest,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> dict[str, Any]:
    """Admin-only: sign a license JWT. Returns token + payload + envelope."""
    svc.authorize_admin(x_admin_token)
    return svc.issue(
        sub=body.sub,
        machine_id=body.machine_id,
        days=body.days,
        tier=body.tier,
        features=body.features,
        max_devices=body.max_devices,
        max_players_per_device=body.max_players_per_device,
    )


@router.post("/import")
async def post_import(
    file: Annotated[UploadFile, File(description="raw JWT (e.g. licence.jwt)")],
) -> dict[str, Any]:
    """User-side import: validate against this host and persist to disk."""
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        from fastapi import HTTPException

        raise HTTPException(status_code=413, detail="license file is too large")
    return svc.import_license_file(content)
