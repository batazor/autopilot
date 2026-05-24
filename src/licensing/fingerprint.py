"""Machine fingerprint for license binding.

Combines three sources to survive Docker rebuilds and uid changes:
    1. ``/etc/machine-id`` (mount ``:ro`` into container) — preferred, stable per-host install
    2. First non-loopback MAC — covers macOS Docker Desktop where ``machine-id`` is the VM's
    3. ``socket.gethostname()`` — fallback / extra entropy

The components are concatenated and SHA-256'd, then base32-encoded (no padding)
and chunked into groups of 4 for easy reading: ``ABCD-EFGH-IJKL-MNOP``.

Truncated to 16 chars (80 bits) — collision risk is irrelevant here since we
only need uniqueness inside a tester pool, and an attacker who can forge a
*matching* fingerprint and a valid JWT signature has bigger problems.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import socket
import uuid
from pathlib import Path

_MACHINE_ID_PATHS = (
    Path("/etc/machine-id"),
    Path("/var/lib/dbus/machine-id"),
)
_FINGERPRINT_LENGTH = 16  # chars after base32, before dashes (= 80 bits)


def _read_machine_id() -> str:
    for path in _MACHINE_ID_PATHS:
        with contextlib.suppress(OSError):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    return ""


def _read_mac() -> str:
    # ``uuid.getnode()`` falls back to a random 48-bit number if no MAC is
    # available; the high bit (bit 0 of the MSB) signals that fallback. Treat
    # it as empty so we don't bake an unstable value into the fingerprint.
    node = uuid.getnode()
    if (node >> 40) & 0x01:
        return ""
    return f"{node:012x}"


def _read_hostname() -> str:
    with contextlib.suppress(OSError):
        return socket.gethostname() or ""
    return ""


def _components() -> dict[str, str]:
    return {
        "machine_id": _read_machine_id(),
        "mac": _read_mac(),
        "hostname": _read_hostname(),
    }


def _format(digest: bytes) -> str:
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    truncated = encoded[:_FINGERPRINT_LENGTH]
    return "-".join(truncated[i : i + 4] for i in range(0, len(truncated), 4))


def generate_fingerprint() -> str:
    """Stable per-host fingerprint, formatted as ``ABCD-EFGH-IJKL-MNOP``."""
    parts = _components()
    joined = "|".join(f"{k}={parts[k]}" for k in sorted(parts))
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return _format(digest)


def fingerprint_components() -> dict[str, str]:
    """Inspectable view of what went into the fingerprint (for support / debug)."""
    return _components()
