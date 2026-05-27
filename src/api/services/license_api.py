"""License API service: fingerprint + status + admin issuer + user import."""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from licensing.fingerprint import fingerprint_components, generate_fingerprint
from licensing.issue import issue_license
from licensing.keys import admin_issuing_available
from licensing.models import LicenseError
from licensing.status import license_status
from licensing.storage import (
    build_envelope,
    extract_token,
    license_path,
    save_license_file,
)
from licensing.verify import verify_license

ADMIN_TOKEN_ENV = "WOS_ADMIN_TOKEN"


def _admin_token() -> str:
    return os.environ.get(ADMIN_TOKEN_ENV, "").strip()


def admin_endpoint_enabled() -> bool:
    """Issuer endpoint is gated by *both* private key present *and* admin token set."""
    return admin_issuing_available() and bool(_admin_token())


def authorize_admin(provided_token: str | None) -> None:
    """Raise 401/403/404 unless the provided token matches the admin secret."""
    if not admin_issuing_available():
        raise HTTPException(status_code=404, detail="license issuing not available on this instance")
    expected = _admin_token()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail=f"set {ADMIN_TOKEN_ENV} in the environment to enable the issuer endpoint",
        )
    if not provided_token or provided_token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


def get_fingerprint() -> dict[str, Any]:
    return {
        "fingerprint": generate_fingerprint(),
        "components": fingerprint_components(),
    }


def get_status() -> dict[str, Any]:
    status = license_status().to_dict()
    status["admin_enabled"] = admin_endpoint_enabled()
    status["license_file"] = str(license_path())
    return status


def issue(
    *,
    sub: str,
    machine_id: str,
    days: int,
    tier: str,
    features: list[str],
    max_devices: int,
    max_players_per_device: int = 3,
) -> dict[str, Any]:
    """Mint a license and return ``{ token, payload, envelope }``."""
    try:
        token, payload = issue_license(
            sub=sub,
            machine_id=machine_id,
            days=days,
            tier=tier,
            features=features,
            max_devices=max_devices,
            max_players_per_device=max_players_per_device,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LicenseError as exc:
        raise HTTPException(status_code=500, detail=exc.reason) from exc
    envelope = build_envelope(token, payload)
    return {"token": token, "payload": payload, "envelope": envelope}


def import_license_file(content: bytes) -> dict[str, Any]:
    """User-side: validate uploaded file and write it to the configured path.

    Refuses files that:
    - aren't a JSON envelope or bare JWT,
    - have an invalid signature,
    - bind a different machine id,
    - have already expired.

    On success returns the new status (so the UI can render the result without
    a follow-up round-trip).
    """
    try:
        token = extract_token(content)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    host_fp = generate_fingerprint()
    try:
        verify_license(token, expected_machine_id=host_fp)
    except LicenseError as exc:
        status_code = 400 if exc.code in {"bad_signature", "bad_payload", "invalid"} else 409
        raise HTTPException(status_code=status_code, detail=exc.reason) from exc

    # Round-trip the envelope through ``extract_token`` -> we already have the
    # token. Reconstruct a clean envelope from the verified claims so a sloppy
    # upload (e.g., raw JWT with no metadata) still produces a tidy file on disk.
    import jwt

    payload = jwt.decode(token, options={"verify_signature": False})
    envelope = build_envelope(token, payload)
    path = save_license_file(envelope)

    return {
        "ok": True,
        "license_file": str(path),
        "status": get_status(),
    }
