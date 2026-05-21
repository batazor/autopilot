"""Stable digests for dashboard SSE revision polling."""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from dashboard.redis_client import (
    QueueRow,
    fetch_queue_rows,
    sort_queue_rows_by_execution_order,
)


def digest(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def queue_pending_fingerprint(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": p.get("task_id"),
            "scheduled_at": p.get("scheduled_at"),
            "priority": p.get("priority"),
            "scenario_key": p.get("scenario_key"),
            "instance_id": p.get("instance_id"),
        }
        for p in pending
    ]


def queue_running_fingerprint(running: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": r.get("task_id"),
            "instance_id": r.get("instance_id"),
            "scenario_key": r.get("scenario_key"),
            "step": r.get("step"),
            "active_scenario": r.get("active_scenario"),
        }
        for r in running
    ]


def pending_rows_fingerprint(rows: list[QueueRow]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": r.task_id,
            "scheduled_at": r.scheduled_at,
            "priority": r.priority,
            "scenario_key": r.task_type,
            "instance_id": r.instance_id,
        }
        for r in rows
    ]


def compute_pending_queue_digest(client: redis.Redis) -> str:
    """Lightweight queue fingerprint (pending only, execution order)."""
    rows = sort_queue_rows_by_execution_order(client, fetch_queue_rows(client))
    return digest({"pending": pending_rows_fingerprint(rows)})


def queue_view_digest(view: dict[str, Any]) -> str:
    summary = {
        "pending": queue_pending_fingerprint(view.get("pending") or []),
        "running": queue_running_fingerprint(view.get("running") or []),
        "history_head": [
            {
                "task_id": h.get("task_id"),
                "success": h.get("success"),
                "finished_at": h.get("finished_at"),
            }
            for h in (view.get("history") or [])[:8]
        ],
    }
    return digest(summary)


def instance_state_fingerprint(row: dict[str, str]) -> dict[str, str]:
    return {
        "state": (row.get("state") or "").strip(),
        "paused": (row.get("paused") or "").strip(),
        "screen": (row.get("current_screen") or "").strip(),
        "task": (
            (row.get("current_scenario") or row.get("current_task_type") or "").strip()
        ),
        "task_id": (row.get("current_task_id") or "").strip(),
        "step": (row.get("last_active_scenario_step") or "").strip(),
        "nav_target": (row.get("nav_target") or "").strip(),
        "active_player": (row.get("active_player") or "").strip(),
        "last_seen_at": (row.get("last_seen_at") or "").strip(),
        "last_error": (row.get("last_error") or "").strip(),
        "queue_blocked_reason": (row.get("queue_blocked_reason") or "").strip(),
        "nav_error": (row.get("nav_error") or "").strip(),
    }


def notifications_fingerprint(items: list[dict[str, Any]], *, tail: int = 5) -> list[str]:
    return [str(item.get("id") or "") for item in items[-tail:]]
