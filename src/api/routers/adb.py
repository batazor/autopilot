"""ADB / devices routes."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.services import adb_api as svc

router = APIRouter(prefix="/api/adb", tags=["adb"])


class BackendUpdateBody(BaseModel):
    """At least one field must be set. Empty string = remove override (auto)."""

    screenshot_backend: str | None = None
    input_backend: str | None = None


@router.get("")
def get_status(
    port_start: int | None = Query(default=None, ge=1, le=65535),
    port_end: int | None = Query(default=None, ge=1, le=65535),
    port_step: int | None = Query(default=None, ge=1, le=65535),
) -> dict[str, object]:
    """Live ADB scan. Optional ``port_start``/``port_end``/``port_step`` override
    the localhost TCP range probed for emulator instances (default 5555-5625/10)."""
    return svc.get_adb_status(
        port_start=port_start,
        port_end=port_end,
        port_step=port_step,
    )


@router.post("/devices/{serial}/register")
def post_register_device(serial: str) -> dict[str, object]:
    """Persist a live ADB serial into the device registry."""
    return svc.register_device(serial)


@router.post("/reconcile")
def post_reconcile_devices() -> dict[str, object]:
    """Ask the running bot supervisor to reconcile workers with the registry."""
    return svc.request_device_reconcile("refresh-scan")


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
