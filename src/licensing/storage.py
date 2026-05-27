"""License file persistence.

The license file is the **raw JWT** — nothing else. All license metadata
(tier, machine_id, max_devices, max_players_per_device, expiry, features)
lives inside the signed JWT and the runtime verifies that signature.

We deliberately do **not** wrap the JWT in a JSON envelope: any field
outside the signature is advisory/untrusted, and shipping a JSON wrapper
invited "but the file says tier=pro" arguments. The token is authoritative.

Default location: ``<repo_root>/license-data/licence.jwt`` — a directory
mount (not a file mount) so Docker can bind it before the file exists and
the UI can write to it.
"""
from __future__ import annotations

import os
from pathlib import Path

from licensing.models import LicenseError

LICENSE_FILE_ENV = "WOS_LICENSE_FILE"
LICENSE_TOKEN_ENV = "WOS_LICENSE"
DEFAULT_FILENAME = "licence.jwt"
DEFAULT_DIRNAME = "license-data"

_PACKAGE_DIR = Path(__file__).resolve().parent


def _repo_root() -> Path:
    return _PACKAGE_DIR.parent.parent


def default_license_path() -> Path:
    """Default on-disk location for the license file."""
    return _repo_root() / DEFAULT_DIRNAME / DEFAULT_FILENAME


def license_path() -> Path:
    """Resolved file path, honoring the ``WOS_LICENSE_FILE`` override."""
    override = os.environ.get(LICENSE_FILE_ENV, "").strip()
    return Path(override) if override else default_license_path()


def extract_token(content: str | bytes) -> str:
    """Pull the JWT out of a license file's content.

    The file is expected to contain a bare JWT (three base64url segments
    separated by ``.``). Whitespace is trimmed; anything else raises.
    """
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    text = text.strip()
    if not text:
        msg = "license file is empty"
        raise LicenseError(msg, code="bad_file")

    # JWT shape check: exactly two dots, no whitespace inside.
    if text.count(".") != 2 or " " in text or "\n" in text or "\t" in text:
        msg = "license file is not a valid JWT (expected three base64url segments separated by dots)"
        raise LicenseError(msg, code="bad_file")
    return text


def load_token_from_file(path: Path | None = None) -> str:
    """Read a token from the license file. Raises if the file is missing or malformed."""
    path = path or license_path()
    if not path.is_file():
        msg = f"license file not found at {path}"
        raise LicenseError(msg, code="missing")
    try:
        content = path.read_bytes()
    except OSError as exc:
        msg = f"failed to read license file at {path}: {exc}"
        raise LicenseError(msg, code="bad_file") from exc
    return extract_token(content)


def save_token_to_file(token: str, path: Path | None = None) -> Path:
    """Atomically write the JWT to disk. Creates parent dir if missing."""
    token = (token or "").strip()
    if not token:
        msg = "refusing to save empty token"
        raise LicenseError(msg, code="bad_file")
    path = path or license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    # ``Path.replace`` is atomic on POSIX even across same-filesystem moves —
    # readers see either the old or new file, never a half-written one.
    tmp.replace(path)
    return path
