"""Parse W3C ``traceparent`` — no OpenTelemetry imports."""
from __future__ import annotations


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
