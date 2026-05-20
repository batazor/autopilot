"""Trace deep-links for operator UIs (Grafana Tempo, etc.)."""
from __future__ import annotations

import os
from urllib.parse import quote


def tempo_trace_url(trace_id: str) -> str:
    """Build a Tempo/Grafana explore URL when ``WOS_TEMPO_TRACE_URL_TEMPLATE`` is set."""
    tid = str(trace_id or "").strip()
    if not tid:
        return ""
    template = (
        os.environ.get("WOS_TEMPO_TRACE_URL_TEMPLATE")
        or os.environ.get("GRAFANA_TEMPO_TRACE_URL_TEMPLATE")
        or ""
    ).strip()
    if not template:
        return ""
    return template.replace("{trace_id}", quote(tid, safe=""))
