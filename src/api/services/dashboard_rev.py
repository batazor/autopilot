"""Redis-cached dashboard revision digests (shared across SSE clients)."""
from __future__ import annotations

from contextlib import suppress
from typing import Any

REV_QUEUE_KEY = "wos:dashboard:rev:queue"
REV_FLEET_KEY = "wos:dashboard:rev:fleet"
REV_INSTANCE_PREFIX = "wos:dashboard:rev:instance:"
REV_PLAYER_PREFIX = "wos:dashboard:rev:player:"
REV_TTL_SECONDS = 300


def _decode(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode()
    return str(raw)


def get_cached_revision(client: Any, key: str) -> str | None:
    try:
        return _decode(client.get(key))
    except Exception:
        return None


def store_revision(client: Any, key: str, revision: str) -> None:
    with suppress(Exception):
        client.set(key, revision, ex=REV_TTL_SECONDS)


def invalidate_revision(client: Any, key: str) -> None:
    with suppress(Exception):
        client.delete(key)


def _revision_keys_for_topic(
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
) -> list[str]:
    if topic == "queue":
        keys = [REV_QUEUE_KEY, REV_FLEET_KEY]
        if instance_id:
            keys.append(f"{REV_INSTANCE_PREFIX}{instance_id}")
        return keys
    if topic == "fleet":
        return [REV_FLEET_KEY]
    if topic == "instance" and instance_id:
        return [f"{REV_INSTANCE_PREFIX}{instance_id}"]
    if topic == "player" and player_id:
        return [f"{REV_PLAYER_PREFIX}{player_id}"]
    return []


def invalidate_revision_for_topic(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
) -> None:
    if client is None:
        return
    for key in _revision_keys_for_topic(
        topic=topic, instance_id=instance_id, player_id=player_id
    ):
        invalidate_revision(client, key)


async def invalidate_revision_async(client: Any, key: str) -> None:
    with suppress(Exception):
        await client.delete(key)  # type: ignore[misc]


async def invalidate_revision_for_topic_async(
    client: Any,
    *,
    topic: str,
    instance_id: str | None = None,
    player_id: str | None = None,
) -> None:
    if client is None:
        return
    for key in _revision_keys_for_topic(
        topic=topic, instance_id=instance_id, player_id=player_id
    ):
        await invalidate_revision_async(client, key)
