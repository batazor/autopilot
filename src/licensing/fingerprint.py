"""Machine fingerprint for license binding.

Three sources, in priority order, survive Docker rebuilds and uid changes:
    1. ``/etc/machine-id`` when available — the stable per-host anchor.
    2. ``license-data/host-id`` — an automatic id persisted in the shared
       license volume, so the bridge-network API and host-network bot agree
       even on Docker Desktop hosts without a usable machine-id.
    3. First non-loopback MAC + ``socket.gethostname()`` — last-resort entropy
       when the shared license volume is unavailable.

Why the shared host-id exists: hostname and MAC can diverge between containers
on the same machine. A ``network_mode: host`` bot may see the host's network
identity while the bridge-network API sees its container identity. The shared
license-data volume is mounted into both services, so it gives one-click
installs a stable fallback without asking users to set environment variables.

The chosen inputs are concatenated and SHA-256'd, then base32-encoded (no
padding) and chunked into groups of 4 for easy reading: ``ABCD-EFGH-IJKL-MNOP``.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from licensing.storage import load_or_create_host_id

_MACHINE_ID_PATHS = (
    Path("/etc/machine-id"),
    Path("/var/lib/dbus/machine-id"),
)
_FINGERPRINT_LENGTH = 16  # chars after base32, before dashes (= 80 bits)


def _safe_read(reader: Callable[[], str]) -> str:
    """Read one host-id component without letting platform quirks break the UI."""
    try:
        return (reader() or "").strip()
    except Exception:
        return ""


def _read_machine_id() -> str:
    for path in _MACHINE_ID_PATHS:
        with contextlib.suppress(OSError):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    return ""


def _read_shared_host_id() -> str:
    return load_or_create_host_id()


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
        "machine_id": _safe_read(_read_machine_id),
        "shared_host_id": _safe_read(_read_shared_host_id),
        "mac": _safe_read(_read_mac),
        "hostname": _safe_read(_read_hostname),
    }


def _format(digest: bytes) -> str:
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    truncated = encoded[:_FINGERPRINT_LENGTH]
    return "-".join(truncated[i : i + 4] for i in range(0, len(truncated), 4))


def _fingerprint_inputs(parts: dict[str, str]) -> dict[str, str]:
    """The subset of components that actually feed the digest.

    ``machine_id`` is the stable per-host anchor when present. Otherwise the
    shared license-data ``host-id`` wins so bridge-network API and host-network
    bot containers still agree. Only when both are unavailable do we fall back
    to ``hostname`` + ``mac`` for entropy.
    """
    if parts.get("machine_id"):
        return {"machine_id": parts["machine_id"]}
    if parts.get("shared_host_id"):
        return {"shared_host_id": parts["shared_host_id"]}
    inputs = {k: v for k, v in parts.items() if k not in {"machine_id", "shared_host_id"} and v}
    if inputs:
        return inputs
    # Extremely locked-down containers can hide every normal host identifier.
    # Keep the endpoint non-raising and deterministic, but make the value visibly synthetic.
    return {"fallback": "host-id-unavailable"}


def generate_fingerprint() -> str:
    """Stable per-host fingerprint, formatted as ``ABCD-EFGH-IJKL-MNOP``."""
    inputs = _fingerprint_inputs(_components())
    joined = "|".join(f"{k}={inputs[k]}" for k in sorted(inputs))
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return _format(digest)


def fingerprint_components() -> dict[str, str]:
    """Inspectable view of what went into the fingerprint (for support / debug)."""
    return _components()
