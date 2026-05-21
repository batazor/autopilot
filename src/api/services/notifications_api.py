"""Notifications API service: reads ``wos:ui:notifications:<id>`` for the dashboard.

The producer side lives in ``ui.notifications`` (worker writes via
``push_ui_notification``); on the consumer side both the Streamlit page and
this FastAPI service want the same de-duplication semantics — return only
events whose ``id`` the caller hasn't seen yet, and skip events older than a
configurable cutoff so a fresh tab doesn't get flooded with stale toasts.

The Streamlit page held its dedup set in ``st.session_state``. Browsers using
the Next.js page keep it client-side and pass it back as ``seen_ids`` query
params; this keeps the API stateless across operator tabs.
"""
from __future__ import annotations

from typing import Any

from dashboard.notifications import pop_new_notifications


def list_notifications(
    client: Any,
    instance_id: str,
    *,
    seen_ids: set[str] | None = None,
    max_age_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Return fresh notifications for ``instance_id``, oldest first.

    ``seen_ids`` IDs are excluded so the same event is never returned to the
    same tab twice; callers append the returned ``id``s to that set on the
    client side.
    """
    return pop_new_notifications(
        client,
        instance_id,
        seen=seen_ids or set(),
        max_age_seconds=max_age_seconds,
    )
