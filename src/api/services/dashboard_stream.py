"""Server-Sent Events for dashboard pages (queue, fleet, instance, approvals)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from adb.approvals import click_approval_enabled
from api.services import fleet, notifications_api, queue_api
from api.services.click_approval_store import (
    _trace_id_from_payload,
    get_pending,
)
from config.devices import load_devices
from config.loader import load_settings
from ui.dashboard_events import CHANNEL
from ui.redis_client import (
    count_claimed_slots,
    count_queue_tasks,
    count_queue_tasks_for_instance,
    fetch_queue_history_rows,
    get_instance_state,
    get_player_state_hash,
)
from ui.reference_preview import rolling_live_preview_path

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.35
HEARTBEAT_INTERVAL_S = 25.0

_VALID_TOPICS = frozenset(
    {"queue", "fleet", "instance", "player", "approval", "notifications"}
)


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


def _instance_state_fingerprint(row: dict[str, str]) -> dict[str, str]:
    return {
        "state": (row.get("state") or "").strip(),
        "paused": (row.get("paused") or "").strip(),
        "screen": (row.get("current_screen") or "").strip(),
        "task": (
            (row.get("current_scenario") or row.get("current_task_type") or "").strip()
        ),
        "active_player": (row.get("active_player") or "").strip(),
        "last_seen_at": (row.get("last_seen_at") or "").strip(),
        "last_error": (row.get("last_error") or "").strip(),
        "queue_blocked_reason": (row.get("queue_blocked_reason") or "").strip(),
        "nav_error": (row.get("nav_error") or "").strip(),
    }


def fleet_revision(client: Any) -> str:
    settings = load_settings()
    instances = settings.instances
    db_registry = load_devices()
    by_name = {str(d.name): d for d in db_registry.devices}
    q = count_queue_tasks(client)
    claimed = count_claimed_slots(client)
    live, paused, busy = fleet.count_live_instances(client, instances)
    inst_rows: list[dict[str, Any]] = []
    for inst in instances:
        iid = str(getattr(inst, "instance_id", ""))
        row = get_instance_state(client, iid)
        entry: dict[str, Any] = {
            "instance_id": iid,
            **_instance_state_fingerprint(row),
            "status": fleet.fleet_status(row),
        }
        dev = by_name.get(iid)
        if dev is not None:
            players: list[dict[str, str]] = []
            for gamer in dev.all_gamers():
                pid = str(gamer.id)
                ps = fleet.read_player_state(client, pid)
                players.append(
                    {
                        "who": pid,
                        "nickname": (ps.get("nickname") or "").strip(),
                        "in_game_id": (ps.get("player_id") or "").strip(),
                        "stove": (ps.get("stove_level") or "").strip(),
                        "kid": (ps.get("kid") or "").strip(),
                    }
                )
            entry["players"] = players
        inst_rows.append(entry)
    return _digest(
        {
            "metrics": {
                "instances": len(instances),
                "queue": q,
                "locks": claimed,
                "live": live,
                "paused": paused,
                "busy": busy,
            },
            "fleet": inst_rows,
        }
    )


def player_revision(client: Any, player_id: str) -> str:
    state = get_player_state_hash(client, player_id)
    # Stable ordering; full hash so OCR/building/hero fields all trigger updates.
    fields = {k: (state.get(k) or "").strip() for k in sorted(state)}
    return _digest({"player_id": player_id, "field_count": len(fields), "fields": fields})


def instance_revision(client: Any, instance_id: str) -> str:
    row = get_instance_state(client, instance_id)
    queue_n = count_queue_tasks_for_instance(client, instance_id=instance_id)
    preview_path = rolling_live_preview_path(instance_id)
    preview_mtime: float | None = None
    if preview_path.is_file():
        preview_mtime = preview_path.stat().st_mtime
    history = fetch_queue_history_rows(client, instance_id=instance_id, limit=5)
    hist_head = [
        {
            "task_id": h.task_id,
            "finished_at": h.finished_at,
            "success": h.success,
        }
        for h in history
    ]
    return _digest(
        {
            "instance_id": instance_id,
            "status": fleet.fleet_status(row),
            "queue_size": queue_n,
            "preview_mtime": preview_mtime,
            "history_head": hist_head,
            **_instance_state_fingerprint(row),
        }
    )


def _sse_line(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _topic_allowed(
    topic: str,
    instance_id: str | None,
    player_id: str | None,
    msg_instance: str,
    msg_player: str,
) -> bool:
    if topic in ("queue", "fleet"):
        return True
    if topic == "player":
        if not player_id:
            return False
        return not msg_player or msg_player == player_id
    if topic in ("instance", "approval", "notifications"):
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
    player_id: str | None,
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
            msg_pid = str(data.get("player_id") or "")
            if topic in active and _topic_allowed(
                topic, instance_id, player_id, msg_iid, msg_pid
            ):
                revisions.pop(topic, None)
                out.append(_sse_line(topic, {"source": "pubsub", **data}))
        except (json.JSONDecodeError, TypeError):
            pass

    if "queue" in active:
        rev = queue_revision(client)
        if revisions.get("queue") != rev:
            revisions["queue"] = rev
            out.append(_sse_line("queue", {"revision": rev, "source": "poll"}))

    if "fleet" in active:
        rev = fleet_revision(client)
        if revisions.get("fleet") != rev:
            revisions["fleet"] = rev
            out.append(_sse_line("fleet", {"revision": rev, "source": "poll"}))

    if instance_id and "instance" in active:
        rev = instance_revision(client, instance_id)
        if revisions.get("instance") != rev:
            revisions["instance"] = rev
            out.append(
                _sse_line(
                    "instance",
                    {"revision": rev, "instance_id": instance_id, "source": "poll"},
                )
            )

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

    if player_id and "player" in active:
        rev = player_revision(client, player_id)
        if revisions.get("player") != rev:
            revisions["player"] = rev
            out.append(
                _sse_line(
                    "player",
                    {"revision": rev, "player_id": player_id, "source": "poll"},
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
    player_id: str | None,
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
            {
                "topics": sorted(active),
                "instance_id": instance_id or "",
                "player_id": player_id or "",
            },
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
                player_id=player_id,
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
