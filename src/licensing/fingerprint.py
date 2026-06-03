"""Machine fingerprint for license binding.

Three sources, in priority order, survive Docker rebuilds and uid changes:
    1. ``/etc/machine-id`` (bind-mount ``:ro`` into every container) — the stable
       per-host anchor. When present it is used **alone**.
    2. First non-loopback MAC — fallback when machine-id is unavailable.
    3. ``socket.gethostname()`` — fallback / extra entropy.

Why machine-id alone wins: multiple containers on one host share its
``/etc/machine-id`` via the bind-mount, but ``hostname`` and ``mac`` diverge
between them — a ``network_mode: host`` container sees the host's hostname/MAC
while a bridge container sees its container-id hostname and a zeroed
(locally-administered) MAC. Mixing those in would make the worker and API
compute *different* fingerprints on the same host, so a machine-bound license
issued for one would be rejected by the other. Anchoring on machine-id keeps
every container on a host in agreement; hostname+mac are only consulted when no
machine-id exists.

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


def _fingerprint_inputs(parts: dict[str, str]) -> dict[str, str]:
    """The subset of components that actually feed the digest.

    ``machine_id`` is the stable per-host anchor: when present it is used alone
    so containers that share a bind-mounted ``/etc/machine-id`` agree regardless
    of network mode. Only when it is missing do we fall back to ``hostname`` +
    ``mac`` for entropy.
    """
    if parts.get("machine_id"):
        return {"machine_id": parts["machine_id"]}
    return {k: v for k, v in parts.items() if k != "machine_id"}


def generate_fingerprint() -> str:
    """Stable per-host fingerprint, formatted as ``ABCD-EFGH-IJKL-MNOP``."""
    inputs = _fingerprint_inputs(_components())
    joined = "|".join(f"{k}={inputs[k]}" for k in sorted(inputs))
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return _format(digest)


def fingerprint_components() -> dict[str, str]:
    """Inspectable view of what went into the fingerprint (for support / debug)."""
    return _components()
