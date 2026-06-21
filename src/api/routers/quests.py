"""Daily quests — the per-player daily-mission checklist + completion status.

Reads the OCR'd ``chapter.daily.tasks`` buffer (+ the ``chapter.daily.refresh``
reset timer) for one player, parses it into structured tasks via the quest reader
(:mod:`games.wos.core.chapter.daily_tasks`), and returns them with a completion
summary for the dashboard's daily-tasks panel. Read-only — no mutation, no device.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.deps import get_redis

router = APIRouter(prefix="/api/quests", tags=["quests"])

_TASKS_FIELD = "chapter.daily.tasks"
_REFRESH_TIMER = "chapter.daily.refresh"


@router.get("/daily/{player_id}")
def get_daily_tasks(player_id: str) -> dict[str, Any]:
    """The player's daily missions parsed into tasks + a completion summary.

    ``read`` is False when the daily list hasn't been OCR'd yet (empty buffer);
    ``refresh_in_s`` is the seconds to the daily reset (None if the timer is
    unread). Pure read of cached state — safe to poll.
    """
    from games.wos.core.chapter.daily_tasks import parse_daily_tasks

    from config.event_timers import event_timer_remaining_seconds, read_event_timer
    from dashboard.redis_client import get_player_state_hash

    state = get_player_state_hash(get_redis(), player_id)
    buffer = state.get(_TASKS_FIELD, "") or ""
    tasks = parse_daily_tasks(buffer)

    timer = read_event_timer(player_id, _REFRESH_TIMER)
    refresh_in_s = event_timer_remaining_seconds(timer) if timer is not None else None

    done = sum(1 for t in tasks if t.done)
    claimable = sum(1 for t in tasks if t.claimable)
    return {
        "player_id": str(player_id),
        "read": bool(buffer.strip()),
        "refresh_in_s": refresh_in_s,
        "summary": {
            "total": len(tasks),
            "done": done,
            "claimable": claimable,
            "open": len(tasks) - done,
        },
        "tasks": [
            {
                "id": t.id,
                "category": t.category,
                "target": t.target,
                "progress": t.progress,
                "claimable": t.claimable,
                "done": t.done,
            }
            for t in tasks
        ],
    }
