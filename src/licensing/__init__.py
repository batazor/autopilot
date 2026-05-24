"""License gate: Ed25519-signed JWTs bind machine fingerprint + feature flags.

Public surface:
    - LicenseClaims, LicenseError, LicenseStatus
    - generate_fingerprint() — stable per host (machine-id + MAC + hostname)
    - verify_license(token, *, expected_machine_id=None) — raises LicenseError
    - load_license_from_env() — reads WOS_LICENSE, verifies against current host
    - license_status() — non-raising probe for the UI / status endpoint
"""
from __future__ import annotations

from licensing.fingerprint import generate_fingerprint
from licensing.models import LicenseClaims, LicenseError, LicenseStatus
from licensing.status import license_status, load_license, load_license_from_env
from licensing.storage import license_path
from licensing.verify import verify_license

__all__ = [
    "LicenseClaims",
    "LicenseError",
    "LicenseStatus",
    "generate_fingerprint",
    "license_path",
    "license_status",
    "load_license",
    "load_license_from_env",
    "verify_license",
]
