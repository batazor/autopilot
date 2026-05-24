"""JWT verification — Ed25519 signature, exp/nbf, optional machine binding."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import jwt

from licensing.keys import load_public_key
from licensing.models import LicenseClaims, LicenseError

ALGORITHM = "EdDSA"
ISSUER = "wos-autopilot"


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _claims_from_payload(payload: dict[str, Any]) -> LicenseClaims:
    features_raw = payload.get("features") or []
    features = [str(f) for f in features_raw] if isinstance(features_raw, list) else []
    return LicenseClaims(
        sub=str(payload.get("sub") or ""),
        machine_id=str(payload.get("machine_id") or ""),
        tier=str(payload.get("tier") or "free"),
        features=features,
        max_devices=int(payload.get("max_devices") or 1),
        issued_at=_coerce_datetime(payload.get("iat")),
        expires_at=_coerce_datetime(payload.get("exp")),
        jti=str(payload["jti"]) if payload.get("jti") else None,
        raw=payload,
    )


def verify_license(
    token: str,
    *,
    expected_machine_id: str | None = None,
) -> LicenseClaims:
    """Decode + verify a license JWT.

    Checks: Ed25519 signature, ``iss``, ``exp`` (PyJWT enforces it), and — when
    ``expected_machine_id`` is given — that the ``machine_id`` claim matches.

    Raises :class:`LicenseError` on any failure; never returns invalid claims.
    """
    token = (token or "").strip()
    if not token:
        msg = "no license token provided"
        raise LicenseError(msg, code="missing")

    public_key = load_public_key()
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "machine_id"]},
        )
    except jwt.ExpiredSignatureError as exc:
        msg = "license token has expired"
        raise LicenseError(msg, code="expired") from exc
    except jwt.InvalidIssuerError as exc:
        msg = "license token issuer mismatch"
        raise LicenseError(msg, code="bad_issuer") from exc
    except jwt.MissingRequiredClaimError as exc:
        msg = f"license token missing required claim: {exc.claim}"
        raise LicenseError(msg, code="bad_payload") from exc
    except jwt.InvalidSignatureError as exc:
        msg = "license token signature is invalid"
        raise LicenseError(msg, code="bad_signature") from exc
    except jwt.InvalidTokenError as exc:
        msg = f"license token is invalid: {exc}"
        raise LicenseError(msg, code="invalid") from exc

    claims = _claims_from_payload(payload)

    if expected_machine_id is not None and claims.machine_id != expected_machine_id:
        msg = (
            f"license bound to a different machine (token: {claims.machine_id}, "
            f"this host: {expected_machine_id})"
        )
        raise LicenseError(msg, code="machine_mismatch")

    return claims
