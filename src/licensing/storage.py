"""License file format + persistence.

The license file is a JSON *envelope* — the JWT plus advisory metadata so the
user can inspect the file without booting the bot:

    {
      "format": "wos-license-v1",
      "issued_to": "alice@example.com",
      "issued_at": "2026-05-25T10:00:00+00:00",
      "expires_at": "2026-06-24T10:00:00+00:00",
      "machine_id": "ABCD-EFGH-IJKL-MNOP",
      "tier": "pro",
      "features": ["heroes", "mail"],
      "token": "eyJhbGc...Mp1tAuyu..."
    }

Only ``token`` is authoritative — the JWT itself is signed and carries the
canonical claims. The other fields are derived at issue-time for convenience.

The loader is forgiving: a file containing *just* a bare JWT string (no JSON
envelope) is accepted too, so power users can hand-roll one with a single
``echo "$TOKEN" > licence.json``.

Default location: ``<repo_root>/license-data/licence.json`` — a directory
mount (not a file mount) so Docker can bind it before the file exists and the
UI can write to it.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from licensing.models import LicenseError

LICENSE_FILE_ENV = "WOS_LICENSE_FILE"
LICENSE_TOKEN_ENV = "WOS_LICENSE"
ENVELOPE_FORMAT = "wos-license-v1"
DEFAULT_FILENAME = "licence.json"
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


def build_envelope(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON envelope around a signed JWT.

    All metadata fields come from the same ``payload`` that was signed —
    nothing the loader trusts is duplicated, so a divergence between
    envelope metadata and JWT claims is harmless (claims win).
    """
    def _iso(field: str) -> str | None:
        ts = payload.get(field)
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
        except (TypeError, ValueError):
            return None

    return {
        "format": ENVELOPE_FORMAT,
        "issued_to": payload.get("sub"),
        "issued_at": _iso("iat"),
        "expires_at": _iso("exp"),
        "machine_id": payload.get("machine_id"),
        "tier": payload.get("tier"),
        "features": list(payload.get("features") or []),
        "max_devices": payload.get("max_devices"),
        "max_players_per_device": payload.get("max_players_per_device"),
        "token": token,
    }


def envelope_bytes(envelope: dict[str, Any]) -> bytes:
    """Stable serialization used for download + on-disk writes."""
    return json.dumps(envelope, indent=2, sort_keys=False).encode("utf-8")


def extract_token(content: str | bytes) -> str:
    """Pull a JWT out of either an envelope JSON or a raw token blob.

    Raises :class:`LicenseError` if neither shape produces a token.
    """
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    text = text.strip()
    if not text:
        msg = "license file is empty"
        raise LicenseError(msg, code="bad_file")

    # Try JSON envelope first.
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        token = str(data.get("token") or "").strip()
        if not token:
            msg = "license file envelope has no 'token' field"
            raise LicenseError(msg, code="bad_file")
        return token

    # Fall back to treating the whole thing as a bare JWT.
    # Minimal sanity check — three base64url segments separated by dots.
    if text.count(".") != 2 or " " in text or "\n" in text:
        msg = "license file is neither a valid JSON envelope nor a bare JWT"
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


def save_license_file(envelope: dict[str, Any], path: Path | None = None) -> Path:
    """Atomically write the envelope to disk. Creates parent dir if missing."""
    path = path or license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(envelope_bytes(envelope))
    # ``Path.replace`` is atomic on POSIX even across same-filesystem moves —
    # readers see either the old or new file, never a half-written one.
    tmp.replace(path)
    return path
