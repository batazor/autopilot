"""Dashboard realtime events (Redis pub/sub).

Workers and API mutations publish lightweight events; the FastAPI SSE
endpoint fans them out to browsers. Subscribers still poll Redis fingerprints
as a fallback when a producer forgets to publish.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL = "wos:events:dashboard"


def publish_dashboard_event(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    reason: str = "",
) -> None:
    """Notify dashboard SSE subscribers (sync Redis client)."""
    if client is None:
        return
    try:
        payload = json.dumps(
            {
                "topic": topic,
                "instance_id": instance_id or "",
                "reason": reason,
            },
            ensure_ascii=False,
        )
        client.publish(CHANNEL, payload)
    except Exception:
        logger.debug("dashboard event publish failed", exc_info=True)


async def publish_dashboard_event_async(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    reason: str = "",
) -> None:
    if client is None:
        return
    try:
        payload = json.dumps(
            {
                "topic": topic,
                "instance_id": instance_id or "",
                "reason": reason,
            },
            ensure_ascii=False,
        )
        await client.publish(CHANNEL, payload)  # type: ignore[union-attr]
    except Exception:
        logger.debug("dashboard event publish failed", exc_info=True)
