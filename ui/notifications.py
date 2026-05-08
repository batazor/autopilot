"""Cross-process UI notifications (Redis-backed, async-write / sync-read).

Pattern
-------
- **Producer** (worker, async): :func:`push_ui_notification` — appends a JSON
  event to a per-instance Redis list, capped to the most recent
  :data:`MAX_RETAINED_NOTIFICATIONS` entries with a TTL so dead instances do
  not leak data.
- **Consumer** (Streamlit UI, sync): :func:`pop_new_notifications` — reads the
  list non-destructively (``LRANGE``) and de-duplicates against a
  caller-provided ``seen`` set so each browser tab fires the toast exactly
  once. Multiple tabs can coexist.

Notifications are **events**, not state — UI must render them via
``st.toast(...)`` (transient) and not rely on them as a data source. The
canonical state of a player / instance lives elsewhere (``wos:player:*:state``,
``wos:instance:*:state``).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Capped list size: keep recent events fresh, never accumulate forever.
MAX_RETAINED_NOTIFICATIONS = 50
# Idle TTL: wipe the queue if the producer disappears for this long.
RETENTION_SECONDS = 600


def _redis_key(instance_id: str) -> str:
    return f"wos:ui:notifications:{instance_id}"


async def push_ui_notification(
    redis_client: Any | None,
    instance_id: str,
    *,
    kind: str,
    message: str,
    level: str = "success",
    event_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Append a UI toast event to ``wos:ui:notifications:<instance_id>``.

    Returns the assigned ``event_id`` (or ``None`` when no Redis client is
    configured / on Redis errors). The shape stored in Redis::

        {
          "id": "<uuid>",
          "ts": 1731030302.123,
          "kind": "exec.fetch_player",
          "message": "Player synced: TestNick (lv 30, fid 765502864)",
          "level": "success",  # success | info | warning | error
          "payload": {...}      # optional, free-form, stays small
        }
    """
    if redis_client is None:
        return None
    inst = (instance_id or "").strip()
    if not inst:
        return None
    eid = (event_id or uuid.uuid4().hex).strip()
    body = {
        "id": eid,
        "ts": time.time(),
        "kind": str(kind or "").strip() or "info",
        "message": str(message or ""),
        "level": str(level or "info").strip().lower() or "info",
    }
    if payload:
        body["payload"] = payload
    try:
        encoded = json.dumps(body, ensure_ascii=False)
    except Exception:
        logger.debug("push_ui_notification: failed to encode payload", exc_info=True)
        return None
    key = _redis_key(inst)
    try:
        await redis_client.lpush(key, encoded)
        await redis_client.ltrim(key, 0, MAX_RETAINED_NOTIFICATIONS - 1)
        await redis_client.expire(key, RETENTION_SECONDS)
    except Exception:
        logger.debug("push_ui_notification: redis write failed", exc_info=True)
        return None
    return eid


def pop_new_notifications(
    sync_redis_client: Any,
    instance_id: str,
    *,
    seen: set[str],
    max_age_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Read pending notifications and return only those not yet in ``seen``.

    The function is **non-destructive** — events stay in Redis until they age
    out (``RETENTION_SECONDS``) or fall off the right end of the capped list,
    so other UI tabs / sessions can also surface them. The caller is
    responsible for adding the returned ``id``s to ``seen``.

    ``max_age_seconds`` filters out historical events (e.g. when a fresh tab
    opens long after the worker fired the toast) so the user is not flooded
    with stale toasts on page load.
    """
    inst = (instance_id or "").strip()
    if not inst:
        return []
    try:
        raw = sync_redis_client.lrange(_redis_key(inst), 0, MAX_RETAINED_NOTIFICATIONS - 1) or []
    except Exception:
        logger.debug("pop_new_notifications: redis read failed", exc_info=True)
        return []

    now = time.time()
    cutoff = now - max(0.0, float(max_age_seconds))
    parsed: list[dict[str, Any]] = []
    for item in raw:
        s = item.decode() if isinstance(item, bytes) else str(item)
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        eid = str(obj.get("id") or "").strip()
        if not eid or eid in seen:
            continue
        try:
            ts = float(obj.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if max_age_seconds > 0 and ts > 0 and ts < cutoff:
            continue
        parsed.append(obj)

    parsed.sort(key=lambda o: float(o.get("ts") or 0.0))
    return parsed
