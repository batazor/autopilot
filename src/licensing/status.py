"""Non-raising license probe used by the API status endpoint and the worker gate.

Token resolution order:
    1. ``WOS_LICENSE`` env var — explicit override (CI, power users)
    2. License file on disk (default: ``<repo>/license-data/licence.jwt``)

The file is the primary distribution mechanism for end users; the env var is
kept as an escape hatch so existing setups don't break.
"""
from __future__ import annotations

import os

from licensing.fingerprint import generate_fingerprint
from licensing.models import LicenseClaims, LicenseError, LicenseStatus
from licensing.storage import (
    LICENSE_TOKEN_ENV,
    license_path,
    load_token_from_file,
)
from licensing.verify import verify_license


def _resolve_token() -> tuple[str, str]:
    """Return ``(token, source)``. ``source`` is ``env`` or ``file``."""
    env_token = os.environ.get(LICENSE_TOKEN_ENV, "").strip()
    if env_token:
        return env_token, "env"
    return load_token_from_file(), "file"  # raises LicenseError(code='missing') if absent


def load_license() -> LicenseClaims:
    """Read the license (env first, then file) and verify against this host.

    Raises :class:`LicenseError` if no source is available, or the token is
    invalid / expired / bound to a different machine.
    """
    try:
        token, _ = _resolve_token()
    except LicenseError as exc:
        if exc.code == "missing":
            # Make the missing-license message actionable — point at the file.
            msg = (
                f"no license found (looked at ${LICENSE_TOKEN_ENV} env var "
                f"and {license_path()})"
            )
            raise LicenseError(msg, code="missing") from exc
        raise
    return verify_license(token, expected_machine_id=generate_fingerprint())


def license_status() -> LicenseStatus:
    """Probe the license without raising — for UI and graceful-degrade callers."""
    machine_id = generate_fingerprint()
    try:
        claims = load_license()
    except LicenseError as exc:
        state = exc.code if exc.code in {"missing", "expired", "machine_mismatch"} else "invalid"
        return LicenseStatus(active=False, state=state, reason=exc.reason, machine_id=machine_id)

    return LicenseStatus(
        active=True,
        state="active",
        sub=claims.sub,
        tier=claims.tier,
        features=list(claims.features),
        expires_at=claims.expires_at,
        days_left=claims.days_until_expiry(),
        machine_id=machine_id,
        max_devices=claims.max_devices,
        max_players_per_device=claims.max_players_per_device,
    )


# Backwards-compat alias — older imports referenced this name.
def load_license_from_env() -> LicenseClaims:
    return load_license()
