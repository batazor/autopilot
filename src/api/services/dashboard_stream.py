"""Server-Sent Events for dashboard pages (queue, approvals)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from adb.approvals import click_approval_enabled
from api.services import notifications_api, queue_api
from api.services.click_approval_store import (
    _trace_id_from_payload,
    get_pending,
)
from ui.dashboard_events import CHANNEL
from ui.redis_client import get_instance_state

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.35
HEARTBEAT_INTERVAL_S = 25.0

_VALID_TOPICS = frozenset({"queue", "approval", "notifications"})


def _digest(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def queue_revision(client: Any) -> str:
    view = queue_api.build_queue_view(client)
    summary = {
        "pending": view.get("pending_count", 0),
        "running": len(view.get("running") or []),
        "history_head": [
            {
                "task_id": h.get("task_id"),
                "success": h.get("success"),
                "finished_at": h.get("finished_at"),
            }
            for h in (view.get("history") or [])[:8]
        ],
    }
    return _digest(summary)


def approval_revision(client: Any, instance_id: str) -> str:
    payload = get_pending(client, instance_id)
    state = get_instance_state(client, instance_id)
    parts = {
        "has_pending": payload is not None,
        "trace_id": _trace_id_from_payload(payload) if payload else "",
        "enabled": click_approval_enabled(instance_id),
        "screen": (state.get("current_screen") or "").strip(),
        "task": (state.get("current_task_type") or state.get("current_scenario") or "").strip(),
    }
    return _digest(parts)


def notifications_revision(client: Any, instance_id: str) -> str:
    items = notifications_api.list_notifications(
        client,
        instance_id,
        seen_ids=set(),
        max_age_seconds=30.0,
    )
    tail = items[-1] if items else {}
    return _digest({"count": len(items), "last_id": tail.get("id", "")})


def _sse_line(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _topic_allowed(topic: str, instance_id: str | None, msg_instance: str) -> bool:
    if topic == "queue":
        return True
    if topic in ("approval", "notifications"):
        if not instance_id:
            return False
        return not msg_instance or msg_instance == instance_id
    return False


def _poll_tick(
    client: Any,
    pubsub: Any,
    *,
    active: set[str],
    instance_id: str | None,
    revisions: dict[str, str],
    last_heartbeat: float,
    loop_time: float,
) -> tuple[list[str], dict[str, str], float]:
    """One non-blocking-ish poll cycle (runs in a worker thread)."""
    out: list[str] = []
    msg = pubsub.get_message(timeout=0.05)
    if msg and msg.get("type") == "message":
        try:
            data = json.loads(msg["data"])
            topic = str(data.get("topic") or "")
            msg_iid = str(data.get("instance_id") or "")
            if topic in active and _topic_allowed(topic, instance_id, msg_iid):
                revisions.pop(topic, None)
                out.append(_sse_line(topic, {"source": "pubsub", **data}))
        except (json.JSONDecodeError, TypeError):
            pass

    if "queue" in active:
        rev = queue_revision(client)
        if revisions.get("queue") != rev:
            revisions["queue"] = rev
            out.append(_sse_line("queue", {"revision": rev, "source": "poll"}))

    if instance_id:
        if "approval" in active:
            rev = approval_revision(client, instance_id)
            if revisions.get("approval") != rev:
                revisions["approval"] = rev
                out.append(
                    _sse_line(
                        "approval",
                        {"revision": rev, "instance_id": instance_id, "source": "poll"},
                    )
                )
        if "notifications" in active:
            rev = notifications_revision(client, instance_id)
            if revisions.get("notifications") != rev:
                revisions["notifications"] = rev
                out.append(
                    _sse_line(
                        "notifications",
                        {
                            "revision": rev,
                            "instance_id": instance_id,
                            "source": "poll",
                        },
                    )
                )

    if loop_time - last_heartbeat >= HEARTBEAT_INTERVAL_S:
        last_heartbeat = loop_time
        out.append(": heartbeat\n\n")
    return out, revisions, last_heartbeat


async def stream_dashboard_events(
    client: Any,
    *,
    topics: set[str],
    instance_id: str | None,
    should_continue: Any,
) -> AsyncIterator[str]:
    """Yield SSE frames until ``should_continue()`` returns False."""
    active = {t for t in topics if t in _VALID_TOPICS}
    if not active:
        active = {"queue"}

    revisions: dict[str, str] = {}
    last_heartbeat = 0.0

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL)

    try:
        yield _sse_line(
            "connected",
            {"topics": sorted(active), "instance_id": instance_id or ""},
        )

        loop = asyncio.get_running_loop()
        while await should_continue():
            now = loop.time()
            lines, revisions, last_heartbeat = await asyncio.to_thread(
                _poll_tick,
                client,
                pubsub,
                active=active,
                instance_id=instance_id,
                revisions=revisions,
                last_heartbeat=last_heartbeat,
                loop_time=now,
            )
            for line in lines:
                yield line
            await asyncio.sleep(POLL_INTERVAL_S)
    finally:
        try:
            pubsub.unsubscribe(CHANNEL)
            pubsub.close()
        except Exception:
            logger.debug("dashboard stream pubsub cleanup failed", exc_info=True)
