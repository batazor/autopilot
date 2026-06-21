"""Parse W3C ``traceparent`` — no OpenTelemetry imports."""
from __future__ import annotations

from typing import Any


def w3c_trace_id_hex(traceparent: str | None) -> str | None:
    """Return the 32-char lowercase hex trace id from a W3C ``traceparent`` value."""

    s = (traceparent or "").strip()
    parts = s.split("-")
    if len(parts) != 4:
        return None
    tid = parts[1]
    if len(tid) != 32:
        return None
    try:
        int(tid, 16)
    except ValueError:
        return None
    return tid.lower()


def trace_id_hex_from_carrier(carrier: dict[str, Any] | None) -> str:
    """Read a 32-hex trace id from queue/approval payload fields."""
    if not carrier:
        return ""
    direct = str(carrier.get("trace_id") or "").strip()
    if direct:
        return direct
    parsed = w3c_trace_id_hex(str(carrier.get("traceparent") or ""))
    return parsed or ""
