"""Dashboard realtime events (Redis pub/sub).

Workers and API mutations publish lightweight events; the FastAPI SSE
endpoint fans them out to browsers. Topics: ``queue``, ``fleet``, ``instance``, ``player``,
``approval``, ``notifications``, ``area`` (area.json / module area.yaml saves).
Subscribers still poll Redis fingerprints
(~350ms) when a producer forgets to publish.
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
    player_id: str | None = None,
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
                "player_id": player_id or "",
                "reason": reason,
            },
            ensure_ascii=False,
        )
        client.publish(CHANNEL, payload)
        _invalidate_revision_cache(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
        )
    except Exception:
        logger.debug("dashboard event publish failed", exc_info=True)


async def publish_dashboard_event_async(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
    reason: str = "",
) -> None:
    if client is None:
        return
    try:
        payload = json.dumps(
            {
                "topic": topic,
                "instance_id": instance_id or "",
                "player_id": player_id or "",
                "reason": reason,
            },
            ensure_ascii=False,
        )
        await client.publish(CHANNEL, payload)  # type: ignore[union-attr]
        await _invalidate_revision_cache_async(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
        )
    except Exception:
        logger.debug("dashboard event publish failed", exc_info=True)


def _throttle_key(
    *,
    topic: str,
    instance_id: str | None,
    player_id: str | None,
) -> str:
    scope = (instance_id or player_id or "global").strip() or "global"
    return f"wos:dashboard:throttle:{topic}:{scope}"


def _invalidate_revision_cache(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
) -> None:
    try:
        from api.services.dashboard_rev import invalidate_revision_for_topic

        invalidate_revision_for_topic(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
        )
    except Exception:
        logger.debug("dashboard revision cache invalidate failed", exc_info=True)


async def _invalidate_revision_cache_async(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
) -> None:
    try:
        from api.services.dashboard_rev import invalidate_revision_for_topic_async

        await invalidate_revision_for_topic_async(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
        )
    except Exception:
        logger.debug("dashboard revision cache invalidate failed", exc_info=True)


def publish_dashboard_event_throttled(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
    min_interval_s: float = 0.25,
    reason: str = "",
) -> None:
    """Publish at most once per ``min_interval_s`` per (topic, instance/player)."""
    if client is None:
        return
    key = _throttle_key(topic=topic, instance_id=instance_id, player_id=player_id)
    try:
        allowed = client.set(key, "1", nx=True, ex=max(1, int(min_interval_s)))
        if not allowed:
            return
        publish_dashboard_event(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
            reason=reason,
        )
    except Exception:
        logger.debug("dashboard throttled publish failed", exc_info=True)


async def publish_dashboard_event_throttled_async(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
    min_interval_s: float = 0.25,
    reason: str = "",
) -> None:
    if client is None:
        return
    key = _throttle_key(topic=topic, instance_id=instance_id, player_id=player_id)
    try:
        allowed = await client.set(  # type: ignore[union-attr]
            key, "1", nx=True, ex=max(1, int(min_interval_s))
        )
        if not allowed:
            return
        await publish_dashboard_event_async(
            client,
            topic=topic,
            instance_id=instance_id,
            player_id=player_id,
            reason=reason,
        )
    except Exception:
        logger.debug("dashboard throttled publish failed", exc_info=True)
