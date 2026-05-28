"""ADB / devices routes."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from api.services import adb_api as svc

router = APIRouter(prefix="/api/adb", tags=["adb"])


class BackendUpdateBody(BaseModel):
    """At least one field must be set. Empty string = remove override (auto)."""

    screenshot_backend: str | None = None
    input_backend: str | None = None


@router.get("")
def get_status() -> dict[str, object]:
    return svc.get_adb_status()


@router.post("/devices/{serial}/reset-display")
def post_reset_device_display(serial: str) -> dict[str, object]:
    """Clear wm size/density overrides on the device (restore physical resolution)."""
    return svc.reset_device_display(serial)


@router.get("/devices/{serial}/scrcpy")
def get_scrcpy_status(serial: str) -> dict[str, object]:
    """Return installed scrcpy-server jar state for ``serial``."""
    return svc.get_scrcpy_status_for(serial)


@router.post("/devices/{serial}/scrcpy/install")
def post_install_scrcpy(serial: str) -> dict[str, object]:
    """Download + push scrcpy-server jar from Genymobile/scrcpy GitHub release."""
    return svc.install_scrcpy_for(serial)


@router.post("/devices/{serial}/backend")
def post_set_device_backend(serial: str, body: BackendUpdateBody) -> dict[str, object]:
    """Rewrite devices.yaml to set/clear per-device screenshot_backend / input_backend.

    Empty string clears the override (smart default); omit a field to leave it unchanged.
    Running workers must be restarted to pick up the change.
    """
    return svc.set_device_backend(
        serial,
        screenshot_backend=body.screenshot_backend,
        input_backend=body.input_backend,
    )
