"""Key resolution: public key always loadable, private key only for the issuer.

The public key ships inside the package (``src/licensing/public_key.pem``) so
the bot can verify offline. The private key lives only on the developer's box
under ``.secrets/license_signer.key`` (gitignored) — its presence enables the
admin issuer endpoint and the ``issue-license`` CLI.

Override paths via env:
    WOS_LICENSE_PUBLIC_KEY   — explicit public key PEM path
    WOS_LICENSE_PRIVATE_KEY  — explicit private key PEM path
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from licensing.models import LicenseError

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_PUBLIC_KEY = _PACKAGE_DIR / "public_key.pem"


def _repo_root() -> Path:
    # ``src/licensing/`` is two levels below the repo root in this layout.
    return _PACKAGE_DIR.parent.parent


def _default_private_path() -> Path:
    return _repo_root() / ".secrets" / "license_signer.key"


def public_key_path() -> Path:
    override = os.environ.get("WOS_LICENSE_PUBLIC_KEY", "").strip()
    return Path(override) if override else _DEFAULT_PUBLIC_KEY


def private_key_path() -> Path:
    override = os.environ.get("WOS_LICENSE_PRIVATE_KEY", "").strip()
    return Path(override) if override else _default_private_path()


def load_public_key() -> Ed25519PublicKey:
    path = public_key_path()
    if not path.is_file():
        msg = f"license public key not found at {path}"
        raise LicenseError(msg, code="public_key_missing")
    pem = path.read_bytes()
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError) as exc:
        msg = f"failed to parse public key at {path}: {exc}"
        raise LicenseError(msg, code="public_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        msg = f"public key at {path} is not Ed25519"
        raise LicenseError(msg, code="public_key_invalid")
    return key


def load_private_key() -> Ed25519PrivateKey:
    path = private_key_path()
    if not path.is_file():
        msg = f"license private key not found at {path}"
        raise LicenseError(msg, code="private_key_missing")
    pem = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        msg = f"failed to parse private key at {path}: {exc}"
        raise LicenseError(msg, code="private_key_invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        msg = f"private key at {path} is not Ed25519"
        raise LicenseError(msg, code="private_key_invalid")
    return key


def admin_issuing_available() -> bool:
    """True iff the issuer endpoint should be exposed on this instance."""
    with contextlib.suppress(LicenseError):
        load_private_key()
        return True
    return False
