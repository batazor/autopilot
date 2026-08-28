"""Share-link registry for the remote-control screen (watch + click).

A helper opens one unguessable URL and gets exactly one instance's live screen
plus the right to tap it — no login, no dashboard access, no instance id in the
URL. That is the whole security model, and it is deliberate: the audience is a
handful of trusted people, so an expiring UUID is the right amount of ceremony.

The link is stable per instance while it lives: re-sharing returns the same
token (with a refreshed TTL) so URLs already sent to helpers keep working.
Revoking mints a new one, which invalidates every link handed out before.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

# 12h: long enough to cover an evening of helping, short enough that a link
# leaked into a chat log stops working on its own.
DEFAULT_TTL_S = 12 * 3600

TAP_CHANNEL_FMT = "wos:events:manual_tap:{instance_id}"
_TOKEN_KEY_FMT = "wos:ui:remote:token:{token}"
_INSTANCE_KEY_FMT = "wos:ui:remote:instance:{instance_id}"


def issue(
    client: redis.Redis, instance_id: str, *, ttl_s: int = DEFAULT_TTL_S
) -> tuple[str, int]:
    """Return ``(token, ttl_s)`` for ``instance_id``, minting one if needed."""
    existing = client.get(_INSTANCE_KEY_FMT.format(instance_id=instance_id))
    token = str(existing) if existing else uuid.uuid4().hex
    pipe = client.pipeline()
    pipe.set(_TOKEN_KEY_FMT.format(token=token), instance_id, ex=ttl_s)
    pipe.set(_INSTANCE_KEY_FMT.format(instance_id=instance_id), token, ex=ttl_s)
    pipe.execute()
    return token, ttl_s


def resolve(client: redis.Redis, token: str) -> str | None:
    """Return the instance behind ``token``, or ``None`` if unknown/expired."""
    if not token:
        return None
    value = client.get(_TOKEN_KEY_FMT.format(token=token))
    return str(value) if value else None


def revoke(client: redis.Redis, instance_id: str) -> None:
    """Kill every link handed out for ``instance_id``."""
    token = client.get(_INSTANCE_KEY_FMT.format(instance_id=instance_id))
    pipe = client.pipeline()
    if token:
        pipe.delete(_TOKEN_KEY_FMT.format(token=str(token)))
    pipe.delete(_INSTANCE_KEY_FMT.format(instance_id=instance_id))
    pipe.execute()


def current_token(client: redis.Redis, instance_id: str) -> tuple[str | None, int]:
    """Return ``(token, seconds_left)`` for ``instance_id`` without minting one."""
    raw = client.get(_INSTANCE_KEY_FMT.format(instance_id=instance_id))
    if not raw:
        return None, 0
    ttl = client.ttl(_INSTANCE_KEY_FMT.format(instance_id=instance_id))
    return str(raw), max(0, int(ttl or 0))


def publish_tap(client: redis.Redis, instance_id: str, x: float, y: float) -> int:
    """Ask the worker to tap ``(x, y)`` — fractions 0..1 of the streamed frame.

    Normalized rather than pixels: the browser knows where the click landed on
    the image it is showing, and only the worker knows that image's real size.
    Returns the number of subscribers, i.e. 0 when no worker is listening.
    """
    payload = json.dumps({"x": round(float(x), 5), "y": round(float(y), 5)})
    return int(client.publish(TAP_CHANNEL_FMT.format(instance_id=instance_id), payload))
